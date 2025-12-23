"""
Vercel Serverless Function: Flood Hazard Level by IP
Simplified handler format for Vercel Python runtime
"""

from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.error
import urllib.parse
import math

FLOOD_DATA_URL = "https://www.dropbox.com/scl/fi/iuf8evgvxf7hhas249vkb/flood_hazard_data.json?rlkey=qzsz2mzox5vxbips03vzv67v1&st=0ybzj3fe&dl=1"

_flood_data_cache = None


def load_flood_data():
    global _flood_data_cache
    if _flood_data_cache is not None:
        return _flood_data_cache
    
    try:
        request = urllib.request.Request(FLOOD_DATA_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=30) as response:
            _flood_data_cache = json.loads(response.read().decode())
        return _flood_data_cache
    except Exception as e:
        print(f"Error loading flood data: {e}")
        return []


def get_ip_location(ip):
    try:
        url = f"https://ipapi.co/{ip}/json/"
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode())
        if 'latitude' in data and 'longitude' in data:
            return {
                'latitude': data['latitude'],
                'longitude': data['longitude'],
                'city': data.get('city', 'Unknown'),
                'region': data.get('region', 'Unknown'),
                'country': data.get('country_name', 'Unknown')
            }
    except:
        pass
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
    x, y = lat_lon_to_web_mercator(lat, lon)
    for feature in flood_data:
        bounds = feature['bounds']
        if not (bounds['minx'] <= x <= bounds['maxx'] and bounds['miny'] <= y <= bounds['maxy']):
            continue
        geometry = feature['geometry']
        if geometry['type'] == 'Polygon':
            for ring in geometry['coordinates']:
                if point_in_polygon(x, y, ring):
                    return feature['hazard']
    return None


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            # Parse query parameters
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            ip = params.get('ip', [None])[0]
            
            # Get IP from headers if not in params
            if not ip:
                ip = self.headers.get('x-forwarded-for', self.headers.get('x-real-ip', '8.8.8.8'))
                if ',' in ip:
                    ip = ip.split(',')[0].strip()
            
            # Get location
            location = get_ip_location(ip)
            if not location:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': f'Could not locate IP: {ip}'}).encode())
                return
            
            # Load flood data
            flood_data = load_flood_data()
            if not flood_data:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Failed to load flood data'}).encode())
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
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response, indent=2).encode())
            
        except Exception as e:
            print(f"ERROR in handler: {e}")
            import traceback
            traceback.print_exc()
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
