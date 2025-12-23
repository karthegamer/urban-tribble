"""
Vercel Serverless Function: Flood Hazard Level by IP
With comprehensive error logging
"""

from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.error
import urllib.parse
import math
import sys
import traceback

FLOOD_DATA_URL = "https://www.dropbox.com/scl/fi/iuf8evgvxf7hhas249vkb/flood_hazard_data.json?rlkey=qzsz2mzox5vxbips03vzv67v1&st=0ybzj3fe&dl=1"

_flood_data_cache = None


def load_flood_data():
    global _flood_data_cache
    if _flood_data_cache is not None:
        print("Using cached flood data")
        return _flood_data_cache
    
    try:
        print(f"Loading flood data from Dropbox...")
        request = urllib.request.Request(FLOOD_DATA_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read().decode()
            _flood_data_cache = json.loads(data)
        print(f"Successfully loaded {len(_flood_data_cache)} flood zones")
        return _flood_data_cache
    except Exception as e:
        print(f"ERROR loading flood data: {type(e).__name__}: {e}")
        traceback.print_exc()
        return []


def get_ip_location(ip):
    try:
        print(f"Looking up location for IP: {ip}")
        url = f"https://ipapi.co/{ip}/json/"
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode())
        
        if 'latitude' in data and 'longitude' in data:
            print(f"Found location: {data.get('city')}, {data.get('region')}")
            return {
                'latitude': data['latitude'],
                'longitude': data['longitude'],
                'city': data.get('city', 'Unknown'),
                'region': data.get('region', 'Unknown'),
                'country': data.get('country_name', 'Unknown')
            }
        else:
            print(f"No coordinates in response: {data}")
    except Exception as e:
        print(f"ERROR getting IP location: {type(e).__name__}: {e}")
        traceback.print_exc()
    return None


def lat_lon_to_web_mercator(lat, lon):
    R = 6378137.0
    x = R * math.radians(lon)
    lat_rad = math.radians(lat)
    y = R * math.log(math.tan(math.pi / 4 + lat_rad / 2))
    return x, y


def point_in_polygon(x, y, polygon):
    inside = False
    n = len(polygon)
    x1, y1 = polygon[-1]
    for i in range(n):
        x2, y2 = polygon[i]
        if min(y1, y2) < y <= max(y1, y2):
            if x <= max(x1, x2):
                if y1 != y2:
                    x_intersection = (y - y1) * (x2 - x1) / (y2 - y1) + x1
                if x1 == x2 or x <= x_intersection:
                    inside = not inside
        x1, y1 = x2, y2
    return inside


def find_flood_hazard(lat, lon, flood_data):
    try:
        x, y = lat_lon_to_web_mercator(lat, lon)
        print(f"Checking coordinates: lat={lat}, lon={lon}, x={x:.2f}, y={y:.2f}")
        
        for feature in flood_data:
            bounds = feature['bounds']
            if not (bounds['minx'] <= x <= bounds['maxx'] and bounds['miny'] <= y <= bounds['maxy']):
                continue
            
            geometry = feature['geometry']
            if geometry['type'] == 'Polygon':
                for ring in geometry['coordinates']:
                    if point_in_polygon(x, y, ring):
                        print(f"Found flood zone: {feature['hazard']}")
                        return feature['hazard']
        
        print("No flood zone found")
        return None
    except Exception as e:
        print(f"ERROR in find_flood_hazard: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        print("========== NEW REQUEST ==========")
        print(f"Path: {self.path}")
        print(f"Headers: {dict(self.headers)}")
        
        try:
            # Parse query parameters
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            ip = params.get('ip', [None])[0]
            
            print(f"Query params: {params}")
            
            # Get IP from headers if not in params
            if not ip:
                ip = self.headers.get('x-forwarded-for', self.headers.get('x-real-ip', '8.8.8.8'))
                if ',' in ip:
                    ip = ip.split(',')[0].strip()
            
            print(f"Using IP: {ip}")
            
            # Get location
            location = get_ip_location(ip)
            if not location:
                print("Failed to get location")
                self.send_json_response(400, {'error': f'Could not locate IP: {ip}'})
                return
            
            # Load flood data
            flood_data = load_flood_data()
            if not flood_data:
                print("Failed to load flood data")
                self.send_json_response(500, {'error': 'Failed to load flood data'})
                return
            
            # Find hazard
            hazard = find_flood_hazard(location['latitude'], location['longitude'], flood_data)
            
            # Send response
            response = {
                'ip': ip,
                'location': location,
                'flood_hazard': hazard if hazard else 'NONE',
                'in_flood_zone': hazard is not None
            }
            
            print(f"Sending response: {response}")
            self.send_json_response(200, response)
            
        except Exception as e:
            print(f"FATAL ERROR: {type(e).__name__}: {e}")
            traceback.print_exc()
            self.send_json_response(500, {'error': f'Internal error: {str(e)}'})
    
    def send_json_response(self, code, data):
        try:
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data, indent=2).encode())
        except Exception as e:
            print(f"ERROR sending response: {e}")
            traceback.print_exc()
    
    def log_message(self, format, *args):
        # Override to prevent duplicate logging
        pass
