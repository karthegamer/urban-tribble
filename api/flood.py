"""
Vercel Serverless Function: Flood Hazard Level by IP
File: api/flood.py

This function:
1. Gets the client's IP address (or accepts one as a parameter)
2. Converts IP to geographic coordinates using geolocation
3. Transforms coordinates to Web Mercator projection (EPSG:3857)
4. Checks which flood hazard polygon contains the point
5. Returns the hazard level
"""

from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.error
from typing import Optional, Dict, List, Tuple
import math

# URL to your flood data JSON (you'll need to host this somewhere)
# Options: Vercel Blob, AWS S3, Google Cloud Storage, or any public URL
FLOOD_DATA_URL = "https://www.dropbox.com/scl/fi/iuf8evgvxf7hhas249vkb/flood_hazard_data.json?rlkey=qzsz2mzox5vxbips03vzv67v1&st=hon966vn&dl=1"

# Cache for flood data (loaded once per cold start)
_flood_data_cache = None


def load_flood_data() -> List[Dict]:
    """
    Load flood data from external URL and cache it.
    This is called once per serverless function cold start.
    """
    global _flood_data_cache
    
    if _flood_data_cache is not None:
        return _flood_data_cache
    
    try:
        with urllib.request.urlopen(FLOOD_DATA_URL) as response:
            _flood_data_cache = json.loads(response.read().decode())
        return _flood_data_cache
    except Exception as e:
        print(f"Error loading flood data: {e}")
        return []


def get_ip_location(ip: str) -> Optional[Dict]:
    """
    Get geographic coordinates for an IP address using ipapi.co
    Returns dict with 'latitude' and 'longitude' or None if failed
    """
    try:
        # Using ipapi.co free tier (no API key needed, 1000 requests/day)
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
    except Exception as e:
        print(f"Error getting IP location: {e}")
    
    return None


def lat_lon_to_web_mercator(lat: float, lon: float) -> Tuple[float, float]:
    """
    Convert WGS84 latitude/longitude to Web Mercator (EPSG:3857) coordinates.
    
    Web Mercator is used by most web mapping services and matches your data format.
    Formula from: https://en.wikipedia.org/wiki/Web_Mercator_projection
    """
    # Earth's radius in meters
    R = 6378137.0
    
    # Convert longitude to x (straightforward)
    x = R * math.radians(lon)
    
    # Convert latitude to y (uses Mercator projection formula)
    lat_rad = math.radians(lat)
    y = R * math.log(math.tan(math.pi / 4 + lat_rad / 2))
    
    return x, y


def point_in_polygon(x: float, y: float, polygon: List[List[float]]) -> bool:
    """
    Check if a point (x, y) is inside a polygon using ray casting algorithm.
    
    Polygon is a list of [x, y] coordinate pairs.
    This uses the "even-odd rule" - count how many times a ray from the point
    crosses the polygon boundary. Odd = inside, Even = outside.
    """
    inside = False
    n = len(polygon)
    
    # Get the last point to start
    x1, y1 = polygon[-1]
    
    for i in range(n):
        x2, y2 = polygon[i]
        
        # Check if the point's y coordinate is between the edge's y coordinates
        if min(y1, y2) < y <= max(y1, y2):
            # Calculate where the ray crosses the edge
            # If the crossing is to the right of the point, toggle inside/outside
            if x <= max(x1, x2):
                if y1 != y2:
                    x_intersection = (y - y1) * (x2 - x1) / (y2 - y1) + x1
                if x1 == x2 or x <= x_intersection:
                    inside = not inside
        
        # Move to next edge
        x1, y1 = x2, y2
    
    return inside


def find_flood_hazard(lat: float, lon: float, flood_data: List[Dict]) -> Optional[str]:
    """
    Find the flood hazard level for a given latitude/longitude.
    
    Process:
    1. Convert lat/lon to Web Mercator coordinates
    2. Filter polygons by bounding box (fast pre-filter)
    3. Check point-in-polygon for remaining candidates
    4. Return the hazard level of the matching polygon
    """
    # Convert coordinates to Web Mercator (same projection as your data)
    x, y = lat_lon_to_web_mercator(lat, lon)
    
    # Check each polygon
    for feature in flood_data:
        bounds = feature['bounds']
        
        # Quick bounding box check first (eliminates most polygons)
        if not (bounds['minx'] <= x <= bounds['maxx'] and 
                bounds['miny'] <= y <= bounds['maxy']):
            continue
        
        # Point is in bounding box, now do precise polygon check
        geometry = feature['geometry']
        if geometry['type'] == 'Polygon':
            # Polygon coordinates are nested: [[[x1,y1], [x2,y2], ...]]
            for ring in geometry['coordinates']:
                if point_in_polygon(x, y, ring):
                    return feature['hazard']
    
    # No matching polygon found
    return None


class handler(BaseHTTPRequestHandler):
    """
    Vercel serverless function handler.
    
    Accepts GET or POST requests:
    - GET with ?ip=X.X.X.X parameter
    - POST with JSON body: {"ip": "X.X.X.X"}
    - If no IP provided, uses the requester's IP
    """
    
    def do_GET(self):
        self.handle_request()
    
    def do_POST(self):
        self.handle_request()
    
    def handle_request(self):
        try:
            # Parse the request to get IP parameter
            ip = None
            
            # Check query parameters for GET requests
            if '?' in self.path:
                query = self.path.split('?', 1)[1]
                params = urllib.parse.parse_qs(query)
                ip = params.get('ip', [None])[0]
            
            # Check body for POST requests
            if self.command == 'POST' and not ip:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    body = self.rfile.read(content_length)
                    data = json.loads(body.decode())
                    ip = data.get('ip')
            
            # Use requester's IP if none provided
            if not ip:
                ip = self.headers.get('X-Forwarded-For', 
                     self.headers.get('X-Real-IP', 
                     self.client_address[0]))
                # X-Forwarded-For can contain multiple IPs, get the first one
                if ',' in ip:
                    ip = ip.split(',')[0].strip()
            
            # Get location from IP
            location = get_ip_location(ip)
            if not location:
                self.send_error_response(400, f"Could not determine location for IP: {ip}")
                return
            
            # Load flood data
            flood_data = load_flood_data()
            if not flood_data:
                self.send_error_response(500, "Failed to load flood hazard data")
                return
            
            # Find flood hazard
            hazard = find_flood_hazard(
                location['latitude'], 
                location['longitude'], 
                flood_data
            )
            
            # Prepare response
            response = {
                'ip': ip,
                'location': {
                    'latitude': location['latitude'],
                    'longitude': location['longitude'],
                    'city': location['city'],
                    'region': location['region'],
                    'country': location['country']
                },
                'flood_hazard': hazard if hazard else 'NONE',
                'in_flood_zone': hazard is not None
            }
            
            self.send_success_response(response)
            
        except Exception as e:
            self.send_error_response(500, f"Internal server error: {str(e)}")
    
    def send_success_response(self, data: Dict):
        """Send a successful JSON response"""
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')  # Enable CORS
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())
    
    def send_error_response(self, code: int, message: str):
        """Send an error JSON response"""
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        error_data = {'error': message}
        self.wfile.write(json.dumps(error_data, indent=2).encode())
