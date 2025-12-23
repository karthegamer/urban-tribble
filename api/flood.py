"""
Vercel Serverless Function: Flood Hazard Level by IP
File: api/flood.py

This function returns flood hazard levels based on IP geolocation.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
import math
from typing import Optional, Dict, List, Tuple

# Dropbox URL for flood data (with dl=1 for direct download)
FLOOD_DATA_URL = "https://www.dropbox.com/scl/fi/iuf8evgvxf7hhas249vkb/flood_hazard_data.json?rlkey=qzsz2mzox5vxbips03vzv67v1&st=0ybzj3fe&dl=1"

# Cache for flood data (loaded once per cold start)
_flood_data_cache = None


def load_flood_data() -> List[Dict]:
    """
    Load flood data from Dropbox and cache it.
    This function loads the data once per serverless function cold start.
    """
    global _flood_data_cache
    
    if _flood_data_cache is not None:
        return _flood_data_cache
    
    try:
        print(f"Loading flood data from: {FLOOD_DATA_URL}")
        # Add user agent to avoid potential blocking
        request = urllib.request.Request(
            FLOOD_DATA_URL,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read().decode()
            _flood_data_cache = json.loads(data)
        print(f"Successfully loaded {len(_flood_data_cache)} flood zones")
        return _flood_data_cache
    except Exception as e:
        print(f"Error loading flood data: {type(e).__name__}: {str(e)}")
        return []


def get_ip_location(ip: str) -> Optional[Dict]:
    """
    Get geographic coordinates for an IP address using ipapi.co
    Returns dict with 'latitude' and 'longitude' or None if failed
    """
    try:
        # Using ipapi.co free tier (no API key needed)
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
    
    Web Mercator is the projection used by your flood data.
    """
    # Earth's radius in meters
    R = 6378137.0
    
    # Convert longitude to x
    x = R * math.radians(lon)
    
    # Convert latitude to y using Mercator projection
    lat_rad = math.radians(lat)
    y = R * math.log(math.tan(math.pi / 4 + lat_rad / 2))
    
    return x, y


def point_in_polygon(x: float, y: float, polygon: List[List[float]]) -> bool:
    """
    Check if a point is inside a polygon using the ray casting algorithm.
    
    This algorithm counts how many times a ray from the point crosses
    the polygon boundary. Odd crossings = inside, even = outside.
    """
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


def find_flood_hazard(lat: float, lon: float, flood_data: List[Dict]) -> Optional[str]:
    """
    Find the flood hazard level for given coordinates.
    
    Process:
    1. Convert lat/lon to Web Mercator coordinates
    2. Filter by bounding boxes (fast pre-filter)
    3. Check point-in-polygon for candidates
    """
    # Convert to Web Mercator projection
    x, y = lat_lon_to_web_mercator(lat, lon)
    
    for feature in flood_data:
        bounds = feature['bounds']
        
        # Quick bounding box check
        if not (bounds['minx'] <= x <= bounds['maxx'] and 
                bounds['miny'] <= y <= bounds['maxy']):
            continue
        
        # Precise polygon check
        geometry = feature['geometry']
        if geometry['type'] == 'Polygon':
            for ring in geometry['coordinates']:
                if point_in_polygon(x, y, ring):
                    return feature['hazard']
    
    return None


def handler(event, context):
    """
    Main Vercel serverless function handler.
    
    This is the entry point for the function. Vercel passes the HTTP
    request details in the 'event' parameter.
    """
    try:
        # Get IP from query parameters or headers
        ip = None
        
        # Check query parameters
        if 'queryStringParameters' in event and event['queryStringParameters']:
            ip = event['queryStringParameters'].get('ip')
        
        # Check headers if no IP in query
        if not ip:
            headers = event.get('headers', {})
            ip = headers.get('x-forwarded-for', 
                 headers.get('x-real-ip', 
                 headers.get('X-Forwarded-For',
                 headers.get('X-Real-IP'))))
            
            # X-Forwarded-For can have multiple IPs
            if ip and ',' in ip:
                ip = ip.split(',')[0].strip()
        
        # Default IP if none found (for testing)
        if not ip:
            ip = '8.8.8.8'
        
        # Get location from IP
        location = get_ip_location(ip)
        if not location:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': f'Could not determine location for IP: {ip}'
                })
            }
        
        # Load flood data
        flood_data = load_flood_data()
        if not flood_data:
            return {
                'statusCode': 500,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'Failed to load flood hazard data'
                })
            }
        
        # Find flood hazard
        hazard = find_flood_hazard(
            location['latitude'], 
            location['longitude'], 
            flood_data
        )
        
        # Prepare response
        response_data = {
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
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(response_data, indent=2)
        }
        
    except Exception as e:
        print(f"Error in handler: {type(e).__name__}: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': f'Internal server error: {str(e)}'
            })
        }
