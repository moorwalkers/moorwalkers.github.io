"""Processes GPX files to generate GeoJSON data, elevation profiles, downloadable GPX tracks, and an HTML index page for the Moor Walkers website. 

Includes utilities for address lookup, track simplification, clustering, and feature splitting.
Main functionalities:
- Parse and simplify GPX tracks.
- Calculate distances, elevation profiles, ascent/descent, and other track statistics.
- Generate GeoJSON features and cluster start points for color assignment.
- Save elevation profile images and downloadable GPX files.
- Split tracks into individual GeoJSON files and create a manifest.
- Generate an interactive HTML index page with filtering options.
Dependencies: geojson, gpxpy, matplotlib, requests, geopy, OSGridConverter, shapely, sklearn, etc.
"""

import http.server
import json
import os
import re
import socketserver
import subprocess
import threading
import time
from datetime import datetime, timedelta
from math import sqrt

import geojson
import gpxpy
import matplotlib.pyplot as plt
import requests
from geopy import distance
from OSGridConverter import latlong2grid
from PIL import Image
from playwright.sync_api import sync_playwright
from shapely.geometry import LineString
from sklearn.cluster import KMeans

# Constants
PORT = 8001

# Determines the number of physical CPU cores on a Windows system.
def get_windows_physical_core_count():
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance -ClassName Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        return int(result.stdout.strip())

    except (OSError, subprocess.SubprocessError, ValueError):
        return os.cpu_count() or 1

# KMeans (via scikit-learn/joblib) attempts to detect the number ofphysical CPU cores when fitting clusters. On newer Windows systems
# this can trigger warnings because the legacy WMIC command is no longer available. Setting LOKY_MAX_CPU_COUNT to the physical core
# count avoids that detection step and suppresses the warning.
os.environ["LOKY_MAX_CPU_COUNT"] = str(get_windows_physical_core_count())

# Custom HTTP request handler that suppresses log messages.
class QuietHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

# Starts a local HTTP server on the specified port using the QuietHTTPRequestHandler.
def start_local_server():
    handler = QuietHTTPRequestHandler
    httpd = socketserver.TCPServer(("", PORT), handler)

    thread = threading.Thread(target=httpd.serve_forever)
    thread.daemon = True
    thread.start()

    return httpd

# Fetches a formatted address from LocationIQ given latitude and longitude coordinates.
def get_address_from_locationiq(lat, lon):
    """Fetches a formatted address from LocationIQ given latitude and longitude coordinates."""
    
    url = "https://us1.locationiq.com/v1/reverse.php"
    api_key = "pk.1e029c5824010fa1167fc1a2996c5b99"
    params = {
        'key': api_key,
        'lat': lat,
        'lon': lon,
        'format': 'json'
    }
    tries = 2
    for attempt in range(tries):
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            break
        except requests.RequestException as e:
            print(f"Error fetching address from LocationIQ: {e}")
            if attempt == 0:
                print("Waiting 30 seconds before retrying...")
                time.sleep(30)
            else:
                print(f"Error fetching address from LocationIQ on further try: {e}")
                input("Press Enter to continue and then update manually...")
                return e
    display_name = response.json().get('display_name')
    if display_name:
        sections = display_name.split(",")
        if sections[0].strip().startswith("UCR"):
            return ",".join(sections[1:4]).strip()
        else:
            return ",".join(sections[0:3]).strip()

# Simplifies a polyline using the Douglas-Peucker algorithm with a specified tolerance.
def douglas_peucker(points, epsilon):
    """Simplifies a polyline using the Douglas-Peucker algorithm with a specified tolerance."""

    def perpendicular_distance(pt, line_start, line_end):
        """Calculate the perpendicular distance from a point to a line segment."""

        if line_start == line_end:
            return sqrt((pt[0] - line_start[0])**2 + (pt[1] - line_start[1])**2)
        x0, y0 = pt
        x1, y1 = line_start
        x2, y2 = line_end
        num = abs((y2 - y1)*x0 - (x2 - x1)*y0 + x2*y1 - y2*x1)
        den = sqrt((y2 - y1)**2 + (x2 - x1)**2)
        return num / den

    def simplify(points, epsilon):
        """Recursively simplifies a list of points using the Ramer-Douglas-Peucker algorithm."""

        dmax = 0.0
        index = 0
        for i in range(1, len(points) - 1):
            d = perpendicular_distance(points[i], points[0], points[-1])
            if d > dmax:
                index = i
                dmax = d
        if dmax > epsilon:
            rec1 = simplify(points[:index+1], epsilon)
            rec2 = simplify(points[index:], epsilon)
            return rec1[:-1] + rec2
        else:
            return [points[0], points[-1]]

    return simplify(points, epsilon)

def create_data(main_geojson):
    """Processes GPX files and updates a GeoJSON FeatureCollection with track data and metadata."""

    # Create an empty list to store the track years
    years = []

    # Load existing feature_collection data from previous version of geoJSON file
    # Load the GeoJSON file
    if os.path.exists(main_geojson):
        with open(main_geojson, "r", encoding="utf-8") as f:
            geojson_data = geojson.load(f)
        # Check if it's a FeatureCollection, and if so load it
        if isinstance(geojson_data, geojson.FeatureCollection):
            features = geojson_data["features"]
            print("Existing data loaded")
        else:
            print(
                "The loaded data is not a FeatureCollection. Creating an empty features list."
            )
            features = []
        # Create features list and load years list from the feature_collection
        for feature in features:
            years.append(feature["properties"]["name"][:4])

    else:
        print("The GeoJSON file does not exist. Creating an empty features list.")
        features = []

    # Create list of existing tracks already loaded as features
    existing_track_list = []
    for feature in features:
        existing_track_list.append(feature["properties"]["name"] + ".gpx")

    # Loop through each file in the input directory
    input_dir = os.path.join(os.getcwd(), "orig_gpx_files")
    file_list = os.listdir(input_dir)
    # Filter out files that exist in existing_track_list
    file_list = [
        filename
        for filename in file_list
        if filename.endswith(".gpx") and filename not in existing_track_list
    ]
    file_list = list(reversed(file_list))
    file_list_length = len(file_list)
    processed_count = 0
    print(f"Processing {file_list_length} new files")

    for processed_count, filename in enumerate(file_list, start=1):
        # Add the year from the filename to the years list
        years.append(filename[:4])

        # Load the GPX file and parse the data
        with open(
            os.path.join(input_dir, filename), "r", encoding="utf-8"
        ) as file_path:
            gpx_data = gpxpy.parse(file_path)

        # Extract the track segments and points from the GPX file
        track_segments = [s for s in gpx_data.tracks[0].segments]
        raw_points = [p for s in track_segments for p in s.points]
        simplified_coords = douglas_peucker([(p.latitude, p.longitude) for p in raw_points], epsilon=0.0001)
        track_points = [p for p in raw_points if (p.latitude, p.longitude) in simplified_coords]


        # Calculate cumulative distances for each point, required for elevation profiles
        total_distance = 0.0
        cumulative_distances = [0.0]  # Starting with 0 distance
        for i in range(1, len(track_points)):
            coord1 = (track_points[i - 1].latitude, track_points[i - 1].longitude)
            coord2 = (track_points[i].latitude, track_points[i].longitude)
            dist = distance.distance(coord1, coord2).miles
            total_distance += dist
            cumulative_distances.append(total_distance)

        # Create a GeoJSON feature LineString with track points and cumulative distance for each point
        line_coords = [
            (p.longitude, p.latitude, p.elevation, cumulative_distances[i])
            for i, p in enumerate(track_points)
        ]

        # Calculate the total distance of the track in km
        total_distance_km = 0
        for i in range(len(track_points) - 1):
            total_distance_km += distance.distance(
                (track_points[i].latitude, track_points[i].longitude),
                (track_points[i + 1].latitude, track_points[i + 1].longitude),
            ).km

        # Calculate the total distance of the track in miles
        total_distance_mi = 0
        for i in range(len(track_points) - 1):
            total_distance_mi += distance.distance(
                (track_points[i].latitude, track_points[i].longitude),
                (track_points[i + 1].latitude, track_points[i + 1].longitude),
            ).miles

        # Determine the overall ascent and descent, measured in meters, across the track by utilizing a 7-Point averaged list of elevations
        # Initialize an empty list to store computed values
        sevenpoint = []
        # Calculate the last index of the track_points list
        max_index = len(track_points) - 1  # Subtract 1 to get the last index

        # Iterate through track_points using index and value
        for idx, val in enumerate(track_points):
            # Append elevations at the edges directly to sevenpoint list
            if idx == 0 or idx == max_index:
                sevenpoint.append(val.elevation)
            # Compute the average elevation based on the immediate neighbors for second and second-to-last elements
            elif idx == 1 or idx == max_index - 1:
                sum =   val.elevation + \
                        track_points[idx-1].elevation + \
                        track_points[idx+1].elevation
                sevenpoint.append(sum / 3)
            # Compute the average elevation based on a window of 5 elements for third and third-to-last elements
            elif idx == 2 or idx == max_index - 2:
                sum =   val.elevation + \
                        track_points[idx-1].elevation + \
                        track_points[idx-2].elevation + \
                        track_points[idx+1].elevation + \
                        track_points[idx+2].elevation
                sevenpoint.append(sum / 5)
            # Compute the average elevation based on a window of 7 elements for the rest of the elements
            else:
                sum =   val.elevation + \
                        track_points[idx - 1].elevation + \
                        track_points[idx - 2].elevation + \
                        track_points[idx - 3].elevation + \
                        track_points[idx + 1].elevation + \
                        track_points[idx + 2].elevation + \
                        track_points[idx + 3].elevation
                sevenpoint.append(sum / 7)

        # Initialize variables to calculate total ascent and descent
        total_ascent_m = 0
        total_descent_m = 0
        # Iterate through computed elevations in sevenpoint to calculate ascent and descent
        previous_elevation = None
        for elevation in sevenpoint:
            # Skip the first elevation since there's no previous elevation to compare
            if previous_elevation is not None:
                elevation_change = elevation - previous_elevation
                # Calculate total ascent and descent based on elevation change
                if elevation_change > 0:
                    total_ascent_m += elevation_change
                elif elevation_change < 0:
                    total_descent_m += elevation_change
            previous_elevation = elevation

        # Calculate the starting and centre coordinates
        # Extract latitude and longitude from the track points
        coordinates = [(point.latitude, point.longitude) for point in track_points]
        # Create a LineString object from the track points
        line = LineString(coordinates)
        # Calculate the center and starting point of the LineString
        center_point = line.centroid
        starting_point = line.coords[0]
        # Extract latitude and longitude from the center point
        center_latitude = center_point.x
        center_longitude = center_point.y
        # Extract latitude and longitude from the starting point
        starting_latitude = starting_point[0]
        starting_longitude = starting_point[1]

        # Calculate the OS Grid Ref
        # Convert latitude and longitude to grid reference
        gridref = str(latlong2grid(starting_latitude, starting_longitude))
        # print(gridref)

        # Generate the Google Maps link to the starting point
        googleMapsLink = (
            "https://www.google.com/maps?q="
            + str(starting_latitude)
            + ","
            + str(starting_longitude)
        )
        # print(googleMapsLink)

        # Generate the elevation profile image link
        elevation_profile_link = (
            "https://moorwalkers.github.io/elevation_profiles/"
            + filename[:-4].replace(" ", "").replace("@", "_")
            + ".png"
        )

        # Generate the elevation profile image link
        thumbnail_link = (
            "https://moorwalkers.github.io/track_thumbnails/"
            + filename[:-4].replace(" ", "").replace("@", "_")
            + ".png"
        )

        # Calculate the start and end times of the track, and then the duration
        start_time = track_points[0].time
        end_time = track_points[-1].time
        duration = end_time - start_time
        duration = timedelta(seconds=duration.seconds)  # Remove milliseconds

        # Calculate a JavaScript compatible datetime
        if filename[:4] == "2020":
            date_string = "2020-07-01 @ 00-00-00"
        else:
            date_string = filename[:-4]
        # Replace @ with T and - with :
        formatted_date_string = date_string.replace('@', 'T').replace('-', ' ')
        # Adjust the format for parsing the date string
        python_date = datetime.strptime(formatted_date_string, "%Y %m %d T %H %M %S")
        # Convert to ISO 8601 format
        iso_date = python_date.isoformat()
        #print(iso_date)

        # Generate the ind_map links
        ind_map_link = (
            "https://moorwalkers.github.io/map.html?track_id="
            + iso_date
        )

        # Create a GeoJSON feature LineString from the GPX file
        feature = geojson.Feature(
            geometry=geojson.LineString(line_coords),
            properties={
                "name": filename[:-4],
                "date": iso_date,
                "distance_km": round(total_distance_km, 2),
                "distance_mi": round(total_distance_mi, 2),
                "duration": str(duration),
                "ascent": int(total_ascent_m),
                "descent": int(total_descent_m),
                "center_lat": center_latitude,
                "center_lon": center_longitude,
                "place_name": get_address_from_locationiq(starting_latitude, starting_longitude),
                "gridref": gridref,
                "googleMapsLink": googleMapsLink,
                "ind_map_link": ind_map_link,
                "elevation_profile_link": elevation_profile_link,
                "thumbnail_link": thumbnail_link,
            },
        )

        # Add the feature to the features list
        features.append(feature)

        processed_percent = f"{(processed_count / file_list_length) * 100:.1f}%"
        print(f"{processed_percent} processed")

    # Sort the features list by name
    features = sorted(features, key=lambda x: x["properties"]["name"], reverse=True)

    # Add or refresh the thumbnail link for every feature.
    # This also updates tracks already stored in moorwalkers.geojson.
    for feature in features:
        track_name = (
            feature["properties"]["name"]
            .replace(" ", "")
            .replace("@", "_")
        )

        feature["properties"]["thumbnail_link"] = (
            "https://moorwalkers.github.io/track_thumbnails/"
            + track_name
            + ".png"
        )

    # Create a GeoJSON feature collection from the features list
    feature_collection = geojson.FeatureCollection(features)

    write_track_colours(feature_collection["features"], "track_colours.json")
    for feature in feature_collection["features"]:
        feature["properties"].pop("colour", None)
        feature["properties"].pop("cluster_label", None)

    # Write the feature collection to the GeoJSON file
    if needs_update(main_geojson, feature_collection):
        with open(
            main_geojson, "w", encoding="utf-8"
        ) as f:
            geojson.dump(feature_collection, f, indent=4)

    # Remove duplicates from 'years' list
    years = list(set(years))
    years.sort(reverse=True)
    # print(years)

    print("Data created")

    return years, feature_collection

def save_tracks_as_elevation_profiles(feature_collection):
    """Create elevation profiles for each track in the feature collection and save them as images."""
    # Create the output directory if it doesn't exist
    output_dir = os.path.join(os.getcwd(), "elevation_profiles")
    os.makedirs(output_dir, exist_ok=True)

    # Find the maximum elevation found in all tracks
    max_elevation = 0
    for feature in feature_collection["features"]:
        coordinates = feature["geometry"]["coordinates"]
        for i in coordinates:
            max_elevation = max(max_elevation, i[2])

    # Round up max elevation to the nearest 100
    remainder = max_elevation % 100
    if remainder != 0:
        max_elevation += 100 - remainder

    # Create elevation profiles for each track
    for feature in feature_collection["features"]:
        track_name = feature["properties"]["name"].replace(" ", "").replace("@", "_")
        output_file = os.path.join(output_dir, track_name + ".png")

        # Check if the file already exists
        if os.path.exists(output_file):
            continue
        
        coordinates = feature["geometry"]["coordinates"]
        
        # Extract elevation and distance data
        elevations = [point[2] for point in coordinates]
        distances = [point[3] for point in coordinates]

        # Plotting elevation against total distance with xkcd style
        with plt.xkcd():
            plt.figure(figsize=(8, 4))
            plt.plot(distances, elevations)
            plt.xlabel('Distance (miles)')
            plt.ylabel('Elevation (m)')

            # Set y-axis limits
            plt.ylim(bottom=0, top=max_elevation)

            # Adjust layout to ensure labels are visible
            plt.tight_layout()
            # Save the graph as an image
            plt.savefig(output_dir + "/" + track_name)
            plt.close()  # Close the figure to prevent overlap
    
    print("Elevation profiles created")

MANUAL_MODE = True
def save_tracks_as_map_screenshots(feature_collection):
    """
    Generate map thumbnails using the live map page.
    """
    output_dir = os.path.join(os.getcwd(), "track_thumbnails")
    os.makedirs(output_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=not MANUAL_MODE
        )
        page = browser.new_page(
            viewport={"width": 1200, "height": 900}
        )
        for feature in feature_collection["features"]:
            track_name = (
                feature["properties"]["name"]
                .replace(" ", "")
                .replace("@", "_")
            )
            output_file = os.path.join(
                output_dir,
                f"{track_name}.png"
            )
            if os.path.exists(output_file):
                continue
            track_id = feature["properties"]["date"]
            url = f"http://localhost:{PORT}/map.html?track_id={track_id}"
            print(f"Generating thumbnail for {track_name}")
            page.goto(url, wait_until="networkidle")

            if MANUAL_MODE:
                print("\n" + "=" * 70)
                print(f"Track: {track_name}")
                print("Move the map, zoom if required, and wait for all tiles to load.")
                input("Press Enter to take the screenshot...")
                print("=" * 70)
            else:
                # Automatic mode
                page.wait_for_timeout(5000)

            # Screenshot map
            page.screenshot(path=output_file)

            image = Image.open(output_file)
            image.thumbnail((400, 250))
            image.save(output_file, "PNG")

        browser.close()
    print("Track thumbnails created")

def write_track_colours(features, output_path):
    """Store clustered track and marker colours separately from track data."""
    colours = [
        "darkred", "green", "red", "blue", "gray", "purple", "black",
        "cadetblue", "darkgreen", "darkblue",
    ]
    track_colours = {}
    if features:
        num_clusters = len(features) // len(colours) + 1
        coordinates = [feature["geometry"]["coordinates"][0][:2] for feature in features]
        labels = KMeans(n_clusters=num_clusters, random_state=0, n_init="auto").fit(
            coordinates
        ).labels_
        for cluster in range(num_clusters):
            colour_idx = 0
            for feature, label in zip(features, labels):
                if label == cluster:
                    track_colours[feature["properties"]["name"]] = colours[colour_idx % len(colours)]
                    colour_idx += 1

    if needs_update(output_path, track_colours):
        with open(output_path, "w", encoding="utf-8") as output_file:
            json.dump(track_colours, output_file, ensure_ascii=False, indent=2, sort_keys=True)


def split_features_to_files(features, output_dir):
    """Splits GeoJSON features into individual files and generates a manifest and marker features."""

    manifest = {}
    marker_features = []
    for feature in features:
        name = feature.get("properties", {}).get("name")
        if not name:
            continue  # Skip features without a name

        # Sanitize filename:
        # Replace '@' with '_'
        safe_name = name.replace('@', '_')
        # Remove any characters that are not alphanumeric, underscore, or hyphen
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in ('_', '-')).rstrip()
        filename = f"{safe_name}.geojson"
        filepath = os.path.join(output_dir, filename)

        # Create single-feature GeoJSON
        feature_geojson = {
            "type": "FeatureCollection",
            "features": [feature]
        }

        if needs_update(filepath, feature_geojson):
            with open(filepath, "w", encoding="utf-8") as out_f:
                json.dump(feature_geojson, out_f, ensure_ascii=False, indent=2)

        # Add track id and relative path to manifest
        track_id = feature["properties"]["date"]
        manifest[track_id] = (
            os.path.join(output_dir, filename)
            .replace("\\", "/")
        )

        # Extract starting location for marker
        geometry = feature.get("geometry", {})
        coords = geometry.get("coordinates", [])
        if geometry.get("type") == "LineString" and coords:
            start_coord = coords[0]
            # Only use lon, lat (ignore elevation/time if present)
            marker_feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": start_coord[:2]
                },
                "properties": feature.get("properties", {})
            }
            marker_features.append(marker_feature)
    return manifest, marker_features

def write_manifest(manifest, manifest_path):
    """Write the manifest dictionary to a JSON file at the specified path."""

    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, ensure_ascii=False, indent=2)

def write_track_markers(marker_features, output_path):
    """Writes marker features to a GeoJSON file at the specified output path."""

    track_markers_geojson = {
        "type": "FeatureCollection",
        "features": marker_features
    }
    if needs_update(output_path, track_markers_geojson):
        with open(output_path, "w", encoding="utf-8") as mf:
            json.dump(track_markers_geojson, mf, ensure_ascii=False, indent=2)

def create_tracks_content_page(years, feature_collection):
    """Create an HTML page with track data to be embedded in an iframe."""

    # HTML template for the start of the tracks content page
    html_start = """\
<!DOCTYPE html>
<html lang="en-GB">
    <head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Tracks Content</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
        }
        h1 {
            text-align: center;
            margin: 0;
            font-size: 2.5em;
        }
        .track-list {
            display: grid;
            gap: 10px;
            padding: 20px;
        }

            /* Define the grid for desktop and tablet screens */
            @media (min-width: 601px) {
                .track-list {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
            }

            @media (min-width: 1081px) {
                .track-list {
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                }
            }

            /* Define the grid for mobile screens: single column with larger text/images */
            @media (max-width: 600px) {
                .track-list {
                    grid-template-columns: 1fr;
                }
                .track {
                    font-size: 1.4em;
                }
                .track-list .track-content {
                    grid-template-areas:
                        "media details"
                        "actions actions";
                }
                .track-content .track-details {
                    display: contents;
                }
                .track-content .track-details-grid {
                    grid-area: details;
                }
                .track-content .track-actions {
                    grid-area: actions;
                    display: grid;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    align-self: stretch;
                }
            }

        .track {
            font-size: 1.0em;
            border: 10px solid #ccc;
            border-radius: 10px;
            padding: 10px;
            text-align: center;
            min-width: 0;
        }

        .track-title {
            text-align: left;
            margin: 10px 0;
            font-weight: bold;
            line-height: 1.2em;
        }

        .track-content {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
            grid-template-areas: "media details";
            gap: 12px;
            align-items: stretch;
            text-align: left;
        }

        .track-media {
            grid-area: media;
        }

        .track-details {
            grid-area: details;
        }

        .track-media,
        .track-details {
            min-width: 0;
            display: flex;
            flex-direction: column;
        }

        .track-actions {
            display: flex;
            flex-direction: column;
            gap: 6px;
            min-width: 0;
        }

        .track-actions .btn {
            text-align: center;
            overflow-wrap: anywhere;
        }

        .track-download {
            margin-top: auto;
            text-align: center;
        }

        .track-media > img {
            margin: 0 auto;
        }

        .profile-preview {
            display: block;
            width: 100%;
            padding: 0;
            border: 0;
            background: transparent;
            cursor: zoom-in;
        }

        .profile-preview:focus-visible {
            outline: 3px solid #0A4478;
            outline-offset: 2px;
        }

        .profile-lightbox {
            max-width: 96vw;
            max-height: 96vh;
            padding: 52px 16px 16px;
            border: 0;
            border-radius: 6px;
            overflow: visible;
        }

        .profile-lightbox::backdrop {
            background: rgba(0, 0, 0, 0.78);
        }

        .profile-lightbox img {
            display: block;
            max-width: calc(96vw - 32px);
            max-height: calc(90vh - 60px);
            width: auto;
            height: auto;
            object-fit: contain;
        }

        .profile-lightbox-close {
            position: absolute;
            top: 6px;
            right: 16px;
            width: 40px;
            min-height: 40px;
            padding: 0;
            border: 0;
            background: transparent;
            color: #111;
            font: 32px/1 Arial, sans-serif;
            cursor: pointer;
        }

        .profile-lightbox-close:hover {
            color: #1f77b4;
        }

        .profile-lightbox-close:focus-visible {
            outline: 3px solid #1f77b4;
            outline-offset: 3px;
        }

        .track img {
            max-width: 100%; /* Set maximum width to fit the container */
            max-height: 100%; /* Set maximum height to fit the container */
            display: block; /* Ensures images resize properly */
            margin: auto; /* Centers the images horizontally */
            border-radius: 10px;
        }
        
        .track-details {
            text-align: left;
        }

        .track-details-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 8px;
            line-height: 1.3;
        }

        .track-details-primary,
        .track-details-secondary {
            min-width: 0;
        }

        .track-detail {
            margin-bottom: 8px;
        }

        .track-detail-label {
            font-weight: bold;
        }
        
        .track-details-distance {
            line-height: 1.3;
        }

        .track-details-ascent-normal,
        .track-details-ascent-bold {
            line-height: 1.3;
        }

        .track-details-ascent-normal .track-detail-label {
            font-weight: normal;
        }

        .track-details-ascent-bold .track-detail-label {
            font-weight: bold;
        }

        /* Custom styles for larger sliders */
        input[type="range"] {
            width: 400px; /* Adjust the width */
        }
        
        .btn {
            display: block;
            padding: 0.5em 1em;
            background-color: #0A4478;
            color: white;
            text-decoration: none;
            border-radius: 5px;
            transition: background-color 0.2s ease;
            font-size: 1.0em;
        }
        .btn:hover {
            background-color: #1A4E87;
        }
        .img-link {
            display: block;
            background: none;
            padding: 0;
            border: 10px solid transparent;
            border-left-width: 20px;
            border-right-width: 10px;
            border-radius: 10px;
        }
    
:root{--navy:#082f52;--blue:#0a4478;--green:#496b43;--ink:#20303b;--muted:#60717c;--line:#dbe3e6;--soft:#f3f6f2;--shadow:0 8px 24px rgba(8,47,82,.09)}
*{box-sizing:border-box}body{margin:0;padding:0 0 2rem;color:var(--ink);background:#fff;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.5}button,input{font:inherit}a:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid #f3c969;outline-offset:3px}
.filter-panel{margin:1rem 1.25rem 2.25rem;padding:clamp(1rem,3vw,1.5rem);border:1px solid var(--line);border-top:5px solid var(--green);border-radius:14px;background:var(--soft);box-shadow:var(--shadow)}.filter-header{display:flex;align-items:end;justify-content:space-between;gap:1rem;margin-bottom:1.25rem}.filter-header h2{margin:0;color:var(--navy);font-size:1.55rem}.filter-header p{margin:.25rem 0 0;color:var(--muted)}.result-count{color:#365331;font-weight:750;white-space:nowrap}.filter-grid{display:grid;grid-template-columns:minmax(220px,1fr) repeat(2,minmax(260px,1.35fr));gap:1.25rem}.filter-group{min-width:0;padding:1rem;border:1px solid var(--line);border-radius:12px;background:#fff}.filter-group-title{display:block;margin-bottom:.7rem;color:var(--navy);font-weight:750}.search-input{width:100%;min-height:44px;padding:.65rem .75rem;border:1px solid #b7c4ca;border-radius:9px}.range-row{display:grid;grid-template-columns:78px 1fr 74px;gap:.65rem;align-items:center}.range-row+.range-row{margin-top:.8rem}.range-row label{color:var(--muted);font-size:.9rem}.range-value{text-align:right;font-weight:700;white-space:nowrap}input[type=range]{width:100%!important;accent-color:var(--blue)}.filter-actions{display:flex;justify-content:flex-end;margin-top:1rem}.reset-button{min-height:42px;padding:.6rem .9rem;border:1px solid var(--blue);border-radius:9px;color:var(--blue);background:#fff;font-weight:700;cursor:pointer}.no-results{display:none;margin:0 1.25rem 2rem;padding:1rem;border-radius:10px;background:#fff4d8;color:#674c08}
.year-heading{display:flex;align-items:baseline;gap:.75rem;margin:2.75rem 1.25rem 1rem;padding-bottom:.6rem;border-bottom:1px solid var(--line);color:var(--navy);font-size:clamp(1.55rem,4vw,2.15rem)}.year-count{color:var(--muted);font-size:.9rem;font-weight:650}.track-list{gap:1rem!important;padding:0 1.25rem!important}.track{padding:1rem!important;border:1px solid var(--line)!important;border-top:5px solid var(--track-color,var(--green))!important;border-radius:14px!important;background:#fff;box-shadow:var(--shadow)}.track-title{margin:0 0 .9rem!important;color:var(--navy)!important;font-size:1.08rem!important;line-height:1.3!important;text-align:left}.track-content{gap:1rem!important}.track img{border-radius:9px}.img-link{padding:0!important;border:0!important;border-radius:9px!important;overflow:hidden}.profile-preview{margin-top:.6rem}.btn{padding:.62rem .8rem!important;border:1px solid var(--blue);border-radius:9px!important;background:#fff!important;color:var(--blue)!important;font-weight:700;text-align:center}.btn:hover{background:#edf4f8!important}.track-download-action{background:var(--blue)!important;color:#fff!important}.track-download-action:hover{background:#07385f!important}.profile-lightbox{border:0!important;border-radius:12px!important;box-shadow:var(--shadow)}.profile-lightbox-close{border-radius:50%!important;background:#edf4f8!important;color:var(--navy)!important}
@media(max-width:1080px){.filter-grid{grid-template-columns:1fr 1fr}.filter-group-search{grid-column:1/-1}}@media(max-width:680px){.filter-panel{margin-inline:.75rem}.filter-header{align-items:flex-start;flex-direction:column}.filter-grid{grid-template-columns:1fr}.filter-group-search{grid-column:auto}.range-row{grid-template-columns:66px 1fr 68px}.track-list{padding-inline:.75rem!important}.year-heading{margin-inline:.75rem}.track-actions{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr));gap:.55rem}}@media(max-width:430px){.track-actions{grid-template-columns:1fr}}
</style>
    </head>
    <body>
        <section class="filter-panel" aria-labelledby="filterTitle"><div class="filter-header"><div><h2 id="filterTitle">Find a walk</h2><p>Search by place, then refine the results by distance or ascent.</p></div><div class="result-count" id="resultCount" role="status" aria-live="polite"></div></div><div class="filter-grid"><div class="filter-group filter-group-search"><label class="filter-group-title" for="placeSearch">Search by place</label><input class="search-input" type="search" id="placeSearch" placeholder="For example, Powder Mills" autocomplete="off"></div><div class="filter-group"><span class="filter-group-title">Distance</span><div class="range-row"><label for="minDistance">Minimum</label><input type="range" id="minDistance" min="0" max="15" value="0"><span class="range-value"><span id="minDistanceValue"></span> mi</span></div><div class="range-row"><label for="maxDistance">Maximum</label><input type="range" id="maxDistance" min="0" max="15" value="15"><span class="range-value"><span id="maxDistanceValue"></span> mi</span></div></div><div class="filter-group"><span class="filter-group-title">Ascent</span><div class="range-row"><label for="minAscent">Minimum</label><input type="range" id="minAscent" min="0" max="1500" value="0"><span class="range-value"><span id="minAscentValue"></span> m</span></div><div class="range-row"><label for="maxAscent">Maximum</label><input type="range" id="maxAscent" min="0" max="1500" value="1500"><span class="range-value"><span id="maxAscentValue"></span> m</span></div></div></div><div class="filter-actions"><button type="button" class="reset-button" id="resetFilters">Reset filters</button></div></section><div class="no-results" id="noResults" role="status">No walks match the selected filters.</div>
    """

    # HTML template for the end of the tracks content page
    html_end = """    
    <dialog class="profile-lightbox" id="profileLightbox" aria-label="Enlarged elevation profile">
        <button class="profile-lightbox-close" type="button" aria-label="Close">&times;</button>
        <img alt="Elevation Profile">
    </dialog>
    </body>
<script>
  function setMaxSliderValues() {
    var tracks = document.querySelectorAll('.track');
    var maxDistance = 0;
    var maxAscent = 0;

    tracks.forEach(function(track) {
      var trackDistance = parseFloat(track.querySelector('.track-details-distance').innerText.trim().split(' ')[0]);
      var ascentDetails = track.querySelector('.track-details-ascent-normal') || track.querySelector('.track-details-ascent-bold');
      var trackAscent = parseFloat(ascentDetails.innerText.trim().split(' ')[1]);

      maxDistance = Math.max(maxDistance, Math.ceil(trackDistance));
      maxAscent = Math.max(maxAscent, Math.ceil(trackAscent / 100) * 100);
    });

    document.getElementById('maxDistance').setAttribute('max', maxDistance);
    document.getElementById('maxAscent').setAttribute('max', maxAscent);
    
    document.getElementById('maxDistance').value = maxDistance;
    document.getElementById('maxAscent').value = maxAscent;

    document.getElementById('maxDistanceValue').innerText = maxDistance;
    document.getElementById('maxAscentValue').innerText = maxAscent;
  }

  function handlePlaceSearch(searchTerm) {
    var tracks = document.querySelectorAll('.track');
    var visibleCount = 0;
    
    tracks.forEach(function(track) {
      var titleText = track.querySelector('.track-title').innerText.toLowerCase();
      var matchesSearch = searchTerm === '' || titleText.includes(searchTerm.toLowerCase());
      
      var trackDistance = parseFloat(track.querySelector('.track-details-distance').innerText.trim().split(' ')[0]);
      var ascentDetails = track.querySelector('.track-details-ascent-normal') || track.querySelector('.track-details-ascent-bold');
      var trackAscent = parseFloat(ascentDetails.innerText.trim().split(' ')[1]);
      
      var minDistance = parseFloat(document.getElementById('minDistance').value);
      var maxDistance = parseFloat(document.getElementById('maxDistance').value);
      var minAscent = parseFloat(document.getElementById('minAscent').value);
      var maxAscent = parseFloat(document.getElementById('maxAscent').value);
      
      var meetsDistanceAscent = (trackDistance >= minDistance && trackDistance <= maxDistance &&
                                trackAscent >= minAscent && trackAscent <= maxAscent);
      
      var isVisible = matchesSearch && meetsDistanceAscent;
      track.style.display = isVisible ? 'block' : 'none';
      
      if (isVisible) visibleCount++;
    });
    
    updateResultCount(visibleCount);
    return visibleCount;
  }

  function updateResultCount(count) {
    var resultCountEl = document.getElementById('resultCount');
    var noResultsEl = document.getElementById('noResults');
    
    if (count > 0) {
      resultCountEl.innerText = count + ' walk' + (count !== 1 ? 's' : '');
      noResultsEl.style.display = 'none';
    } else {
      resultCountEl.innerText = '';
      noResultsEl.style.display = 'block';
    }
  }

  function filterTracks() {
    var searchTerm = document.getElementById('placeSearch').value;
    handlePlaceSearch(searchTerm);
  }

  function resetAllFilters() {
    document.getElementById('placeSearch').value = '';
    document.getElementById('minDistance').value = '0';
    document.getElementById('maxDistance').value = '15';
    document.getElementById('minAscent').value = '0';
    document.getElementById('maxAscent').value = '1500';
    
    setMaxSliderValues();
    filterTracks();
  }

  document.getElementById('minDistance').addEventListener('input', filterTracks);
  document.getElementById('maxDistance').addEventListener('input', filterTracks);
  document.getElementById('minAscent').addEventListener('input', filterTracks);
  document.getElementById('maxAscent').addEventListener('input', filterTracks);
  document.getElementById('placeSearch').addEventListener('input', filterTracks);
  document.getElementById('resetFilters').addEventListener('click', resetAllFilters);

  setMaxSliderValues();
  filterTracks();

    const profileLightbox = document.getElementById('profileLightbox');
    const profileLightboxImage = profileLightbox.querySelector('img');
    const profileLightboxClose = profileLightbox.querySelector('.profile-lightbox-close');

    document.querySelectorAll('.profile-preview').forEach(function(preview) {
        preview.addEventListener('click', function() {
            const image = preview.querySelector('img');
            profileLightboxImage.src = image.src;
            profileLightboxImage.alt = image.alt;
            profileLightbox.showModal();
            profileLightboxClose.focus();
        });
    });

    profileLightboxClose.addEventListener('click', function() {
        profileLightbox.close();
    });

    profileLightbox.addEventListener('click', function(event) {
        if (event.target === profileLightbox) {
            profileLightbox.close();
        }
    });

    let trackManifestPromise;

    function escapeGpxXml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&apos;');
    }

    function sanitizeGpxFilename(name) {
        const safeName = String(name || 'track')
            .replace(/[^A-Za-z0-9._ -]/g, '_')
            .trim();
        return (safeName || 'track').replace(/\\.gpx$/i, '') + '.gpx';
    }

    async function downloadTrackAsGpx(trackId) {
        try {
            trackManifestPromise ||= fetch('tracks_manifest.json').then(response => {
                if (!response.ok) throw new Error('Unable to load track manifest');
                return response.json();
            });
            const manifest = await trackManifestPromise;
            const trackPath = manifest[trackId];
            if (!trackPath) throw new Error('Track not found in manifest');

            const response = await fetch(trackPath);
            if (!response.ok) throw new Error('Unable to load track data');
            const geojson = await response.json();
            const feature = geojson.type === 'Feature' ? geojson : geojson.features?.[0];
            const coordinates = feature?.geometry?.coordinates;
            if (feature?.geometry?.type !== 'LineString' || !Array.isArray(coordinates) || !coordinates.length) {
                throw new Error('Track has no supported line geometry');
            }

            const trackName = feature.properties?.name || 'Track';
            const trackPoints = coordinates.map(coordinate => {
                const [longitude, latitude, elevation] = coordinate;
                if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) {
                    throw new Error('Track contains invalid coordinates');
                }
                const pointLines = [`    <trkpt lat="${latitude}" lon="${longitude}">`];
                if (Number.isFinite(elevation)) pointLines.push(`      <ele>${elevation}</ele>`);
                pointLines.push('    </trkpt>');
                return pointLines.join('\\n');
            }).join('\\n');

            const gpx = [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<gpx version="1.1" creator="Moor Walkers" xmlns="http://www.topografix.com/GPX/1/1">',
                '  <trk>',
                `    <name>${escapeGpxXml(trackName)}</name>`,
                '    <trkseg>',
                trackPoints,
                '    </trkseg>',
                '  </trk>',
                '</gpx>'
            ].join('\\n');
            const objectUrl = URL.createObjectURL(new Blob([gpx], { type: 'application/gpx+xml;charset=utf-8' }));
            const link = document.createElement('a');
            link.href = objectUrl;
            link.download = sanitizeGpxFilename(trackName);
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(objectUrl);
        } catch (error) {
            console.error('[GPX Download] Error generating GPX:', error);
            alert('Unable to generate the GPX file for this track.');
        }
    }

    document.addEventListener('click', event => {
        const downloadLink = event.target.closest('.track-actions a[href*="map.html?track_id="]');
        if (!downloadLink) return;
        event.preventDefault();
        const trackId = new URL(downloadLink.href).searchParams.get('track_id');
        if (trackId) downloadTrackAsGpx(trackId);
    });
  
  function sendHeight() {
      const height = document.body.scrollHeight;
      window.parent.postMessage({ type: 'resize', height: height }, '*');
  }
  
  window.addEventListener('load', sendHeight);
  window.addEventListener('resize', sendHeight);
  
  setTimeout(sendHeight, 1000);
</script>
</html>
    """

    # Output file path
    output_file = os.path.join(os.getcwd(), "tracks_content.html")

    with open(output_file, "w") as f:
        # Write the start of the HTML page to the output file
        f.write(html_start)

        # Iterate through the specified years
        for year in years:
            if int(year) <= 2020:
                display_year = "2020 or Earlier"
            else:
                display_year = year

            # HTML template for the year section
            if int(year) >= 2020:
                html_year = f"""\
    <h1>{display_year}</h1>
    <div class="track-list">
            """
                f.write(html_year)

                # Iterate through the features in the GeoJSON collection
                for feature in feature_collection["features"]:
                    if int(feature["properties"]["name"][:4]) <= 2020:
                        track_year = '2020'
                    else:
                        track_year = feature["properties"]["name"][:4]
                    if track_year == year:
                        # Set the grid box background colour
                        grid_colour = ""
                        if feature["properties"]["distance_mi"] < 5:
                            grid_colour = "#C0FFC0"
                        if (
                            feature["properties"]["distance_mi"] >= 5
                            and feature["properties"]["distance_mi"] < 6
                        ):
                            grid_colour = "#90EE90"
                        if (
                            feature["properties"]["distance_mi"] >= 6
                            and feature["properties"]["distance_mi"] < 7
                        ):
                            grid_colour = "#F1C40F"
                        if feature["properties"]["distance_mi"] >= 7:
                            grid_colour = "#FFA07A"

                        # Set the ascent / descent font weight
                        asc_desc_font_weight = ""
                        if feature["properties"]["ascent"] < 500:
                            asc_desc_font_weight = "normal"
                        else:
                            asc_desc_font_weight = "bold"

                        # Set the Date/Title
                        # Define a regular expression pattern for a date in the format "YYYY-MM-DD"
                        date_pattern = r"\d{4}-\d{2}-\d{2}"
                        # Ues the date_pattern to identify and then set the 2 date/title types
                        if re.match(date_pattern, feature["properties"]["name"]):
                            date_title = feature["properties"]["name"][:10]
                        else:
                            date_title = feature["properties"]["name"]

                        # HTML template for each track
                        html_track = f"""\
<div class="track" style="--track-color: {grid_colour};">
                <div class="track-title">{feature['properties']['place_name']}</div>
                <div class="track-content">
                    <div class="track-media">
                        <a href="{feature['properties']['ind_map_link']}" target="_blank" class="img-link"><img src="{feature['properties']['thumbnail_link']}" alt="Route map thumbnail" loading="lazy" style="display: block;"></a>
                        <button type="button" class="profile-preview" aria-label="Enlarge elevation profile">
                            <img src="{feature['properties']['elevation_profile_link']}" alt="Elevation profile" loading="lazy">
                        </button>
                    </div>
                    <div class="track-details">
                        <div class="track-details-grid">
                        <div class="track-details-primary">
                            <div class="track-detail">{feature['properties']['gridref']}</div>
                            <div class="track-detail">Date: {date_title}</div>
                        </div>
                        <div class="track-details-secondary">
                            <div class="track-detail">
                                <div class="track-detail-label">Distance:</div>
                                <div class="track-details-distance">
                                    <div>{feature['properties']['distance_mi']} miles</div>
                                    <div>{feature['properties']['distance_km']} km</div>
                                </div>
                            </div>
                            <div class="track-detail">Duration: {feature['properties']['duration']}</div>
                            <div class="track-details-ascent-{asc_desc_font_weight}">
                                <div><span class="track-detail-label">Ascent:</span> {feature['properties']['ascent']}m</div>
                                <div><span class="track-detail-label">Descent:</span> {feature['properties']['descent']}m</div>
                            </div>
                        </div>
                        </div>
                        <div class="track-actions">
                        <a href=\"{feature['properties']['googleMapsLink']}\" target=\"_blank\" class="btn">Starting Location</a>
                        <a href=\"{feature['properties']['ind_map_link']}\" target=\"_blank\" class="btn track-download-action">Download GPX Track File</a>
                        </div>
                    </div>
                </div>
            </div>
                    """
                        f.write(html_track)

                # Close the track-list section for the current year
                f.write("</div>")

        # Write the end of the HTML page
        f.write(html_end)

    print("Tracks content page created")

def group_features_by_year(features):
    """Group features by year extracted from properties.date (ISO 8601 format)."""
    yearly = {}
    for feature in features:
        date_str = feature.get("properties", {}).get("date")
        if not date_str:
            raise ValueError(f"Feature missing date property: {feature.get('properties', {}).get('name', 'unknown')}")
        try:
            year = int(date_str[:4])
        except (ValueError, IndexError):
            raise ValueError(f"Cannot parse year from date '{date_str}' for feature {feature.get('properties', {}).get('name', 'unknown')}")
        
        if year not in yearly:
            yearly[year] = []
        yearly[year].append(feature)
    
    return yearly

def create_yearly_geojson_features(yearly_groups):
    """Create GeoJSON FeatureCollections for each year with deterministic ordering."""
    yearly_geojsons = {}
    
    for year, features in yearly_groups.items():
        # Sort by date (primary) then name (secondary) for deterministic ordering
        sorted_features = sorted(
            features,
            key=lambda f: (f.get("properties", {}).get("date", ""), f.get("properties", {}).get("name", ""))
        )
        
        yearly_geojsons[year] = {
            "type": "FeatureCollection",
            "features": sorted_features
        }
    
    return yearly_geojsons

def needs_update(existing_path, new_geojson):
    """Check if existing GeoJSON file differs from new content (compares parsed JSON, not strings)."""
    if not os.path.exists(existing_path):
        return True
    
    try:
        with open(existing_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        
        # Compare parsed JSON structures
        return existing != new_geojson
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return True

def write_yearly_geojson_files(yearly_geojsons, output_dir):
    """Write yearly GeoJSON files with incremental updates. Returns stats."""
    created = []
    updated = []
    unchanged = []
    
    os.makedirs(output_dir, exist_ok=True)
    
    for year, geojson_data in yearly_geojsons.items():
        filename = f"tracks_{year}.geojson"
        filepath = os.path.join(output_dir, filename)
        
        # Check if file existed before any operation
        file_existed = os.path.exists(filepath)
        
        if needs_update(filepath, geojson_data):
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)
            
            # Classify as created or updated based on pre-write state
            if file_existed:
                updated.append(year)
            else:
                created.append(year)
        else:
            unchanged.append(year)
    
    return created, updated, unchanged

def remove_obsolete_yearly_files(output_dir, valid_years):
    """Remove yearly files for years no longer in the dataset."""
    removed = []
    
    if not os.path.exists(output_dir):
        return removed
    
    for filename in os.listdir(output_dir):
        if not filename.startswith("tracks_") or not filename.endswith(".geojson"):
            continue
        
        # Extract year from filename
        try:
            year_str = filename[7:-8]  # Remove "tracks_" and ".geojson"
            year = int(year_str)
            
            if year not in valid_years:
                filepath = os.path.join(output_dir, filename)
                os.remove(filepath)
                removed.append(year)
        except ValueError:
            continue
    
    return removed

def write_yearly_manifest(yearly_geojsons, manifest_path):
    """Write/update yearly manifest file. Returns True if updated, False if unchanged."""
    manifest = {}
    
    for year in sorted(yearly_geojsons.keys()):
        # Use forward slashes for browser compatibility
        manifest[str(year)] = f"tracks/tracks_{year}.geojson"
    
    if needs_update(manifest_path, manifest):
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return True
    
    return False

def validate_yearly_output(features, yearly_geojsons):
    """Validate yearly output matches source features."""
    total_yearly = sum(len(gc["features"]) for gc in yearly_geojsons.values())
    if total_yearly != len(features):
        raise ValueError(
            f"Reconciliation failed: {len(features)} source features but "
            f"{total_yearly} features in yearly bundles"
        )

def process_yearly_geojson(features, output_dir, yearly_manifest_path):
    """Orchestrate yearly GeoJSON bundling: group, create, write, validate, report."""
    print("\n--- Yearly GeoJSON Generation ---")
    
    # Group features by year
    yearly_groups = group_features_by_year(features)
    
    # Create yearly GeoJSON FeatureCollections
    yearly_geojsons = create_yearly_geojson_features(yearly_groups)
    
    # Validate before writing
    validate_yearly_output(features, yearly_geojsons)
    
    # Write yearly files
    created, updated, unchanged = write_yearly_geojson_files(yearly_geojsons, output_dir)
    
    # Remove obsolete yearly files
    removed = remove_obsolete_yearly_files(output_dir, set(yearly_geojsons.keys()))
    
    # Write/update manifest
    manifest_updated = write_yearly_manifest(yearly_geojsons, yearly_manifest_path)
    
    # Report status for each file
    for year in sorted(yearly_geojsons.keys()):
        if year in created:
            status = "Created"
        elif year in updated:
            status = "Updated"
        else:
            status = "Unchanged"
        print(f"  {status} tracks_{year}.geojson")
    
    if manifest_updated:
        print("  Updated tracks_yearly_manifest.json")
    else:
        print("  Unchanged tracks_yearly_manifest.json")
    
    # Print summary
    print("\nYearly GeoJSON Summary:")
    print(f"  Total features: {len(features)}")
    print(f"  Years represented: {len(yearly_geojsons)}")
    print(f"  Files created: {len(created)}")
    print(f"  Files updated: {len(updated)}")
    print(f"  Files unchanged: {len(unchanged)}")
    print(f"  Files removed: {len(removed)}")

def main():
    """Processes GeoJSON map data to generate elevation profiles, GPX files, split tracks, and supporting files."""

    # Paths
    main_geojson = "moorwalkers.geojson"
    output_dir = "tracks"
    manifest_path = "tracks_manifest.json"
    track_markers_path = "track_markers.geojson"

    # Create the main GeoJSON file with all tracks
    years, feature_collection = create_data(main_geojson)

    # Create individual elevation profile images
    save_tracks_as_elevation_profiles(feature_collection)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Load GeoJSON
    with open(main_geojson, "r", encoding="utf-8") as f:
        data = json.load(f)
    features = data.get("features", [])

    # Split features into individual files and create manifest and marker features
    manifest, marker_features = split_features_to_files(features, output_dir)
    write_manifest(manifest, manifest_path)
    write_track_markers(marker_features, track_markers_path)

    print(f"Split {len(features)} features into '{output_dir}' folder, created manifest '{manifest_path}', and created '{track_markers_path}' with {len(marker_features)} markers.")
    
    # Generate yearly GeoJSON bundles and manifest
    yearly_manifest_path = "tracks_yearly_manifest.json"
    process_yearly_geojson(features, output_dir, yearly_manifest_path)
    
    # Create the tracks content page
    create_tracks_content_page(years, feature_collection)

    # Create individual map and track thumbnails
    server = start_local_server()
    # Ensure the local server is running before taking screenshots
    try:
        save_tracks_as_map_screenshots(feature_collection)
    finally:
        server.shutdown()

if __name__ == "__main__":
    main()
