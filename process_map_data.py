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

import json
import os
import re
import time
from datetime import datetime, timedelta
from math import sqrt
from xml.etree.ElementTree import Element, SubElement, tostring

import geojson
import gpxpy
import matplotlib.pyplot as plt
import requests
from geopy import distance
from OSGridConverter import latlong2grid
from shapely.geometry import LineString
from sklearn.cluster import KMeans

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
            feature_collection = geojson_data
            print("Existing data loaded")
        else:
            print(
                "The loaded data is not a FeatureCollection. Creating an empty features list."
            )
            features = []
        # Create features list and load years list from the feature_collection
        features = feature_collection["features"]
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

    for filename in file_list:
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
                sevenpoint.append(track_points[idx].elevation)
            # Compute the average elevation based on the immediate neighbors for second and second-to-last elements
            elif idx == 1 or idx == max_index - 1:
                sum =   track_points[idx].elevation + \
                        track_points[idx-1].elevation + \
                        track_points[idx+1].elevation
                sevenpoint.append(sum / 3)
            # Compute the average elevation based on a window of 5 elements for third and third-to-last elements
            elif idx == 2 or idx == max_index - 2:
                sum =   track_points[idx].elevation + \
                        track_points[idx-1].elevation + \
                        track_points[idx-2].elevation + \
                        track_points[idx+1].elevation + \
                        track_points[idx+2].elevation
                sevenpoint.append(sum / 5)
            # Compute the average elevation based on a window of 7 elements for the rest of the elements
            else:
                sum =   track_points[idx].elevation + \
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
        for idx, val in enumerate(sevenpoint):
            # Skip the first index since there's no previous elevation to compare
            if idx != 0:
                elevation_change = sevenpoint[idx] - sevenpoint[idx-1]
                # Calculate total ascent and descent based on elevation change
                if elevation_change > 0:
                    total_ascent_m += elevation_change
                elif elevation_change < 0:
                    total_descent_m += elevation_change

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

        # Generate the track_download link
        download_link = (
            "https://moorwalkers.github.io/track_downloads/"
            + filename[:-4].replace(" ", "").replace("@", "_")
            + ".gpx"
        )
        # print(download_link)

        # Generate the elevation profile image link
        elevation_profile_link = (
            "https://moorwalkers.github.io/elevation_profiles/"
            + filename[:-4].replace(" ", "").replace("@", "_")
            + ".png"
        )
        #print(elevation_profile_link)

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
            "https://moorwalkers.github.io/map_std.html?track_id="
            + iso_date
        )
        # print(ind_map_link)
        ind_map_link_os = (
            "https://moorwalkers.github.io/map_os.html?track_id="
            + iso_date
        )
        # print(ind_map_link_os)

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
                "download_link": download_link,
                "ind_map_link": ind_map_link,
                "ind_map_link_os": ind_map_link_os,
                "elevation_profile_link": elevation_profile_link,
            },
        )

        # Add the feature to the features list
        features.append(feature)

        processed_count += 1
        processed_percent = "{:.1f}%".format((processed_count / file_list_length) * 100)
        print(f"{processed_percent} processed")

    # Sort the features list by name
    features = sorted(features, key=lambda x: x["properties"]["name"], reverse=True)

    # Create a GeoJSON feature collection from the features list
    feature_collection = geojson.FeatureCollection(features)

    # Cluster the GeoJSON data into groups of nearby start points
    # Required to decrease the likelihood of the same colour being used for nearby tracks

    # List of colours to be used for the tracks and markers
    colours = [
        "darkred",
        "green",
        "red",
        "blue",
        "gray",
        "purple",
        "black",
        "cadetblue",
        "darkgreen",
        "orange",
        "darkblue",
    ]

    # Calulcate how many clusters should be produced by dividing the total number of tracks by the
    # total number of available colours and adding one, this should ensure no cluster has more
    # tracks than available colours
    num_clusters = len(feature_collection["features"]) // len(colours) + 1
    # print(f"Number of featues = {len(feature_collection['features'])}")
    # print(f"Number of colours = {len(colours)}")
    # print(f"Number of clusters = {num_clusters}")

    # Extract the start point coordinates from GeoJSON file
    coordinates = []
    for feature in feature_collection["features"]:
        # Use the first point in each linestring, just returning the lat and long (not elevation)
        first_point = feature["geometry"]["coordinates"][0][:2]
        coordinates.append(first_point)

    # Cluster coordinates using KMeans algorithm
    kmeans = KMeans(n_clusters=num_clusters, random_state=0, n_init="auto").fit(
        coordinates
    )
    labels = kmeans.labels_

    # Add the cluster label property to each feature in the GeoJSON data
    for i, feature in enumerate(feature_collection["features"]):
        feature["properties"]["cluster_label"] = int(labels[i])

    # Add the colour property to each feature in the GeoJSON data
    # This should assign a different colour from the colours list to each feature within a cluster
    for i in range(num_clusters):
        colour_idx = 0
        for feature in feature_collection["features"]:
            if feature["properties"]["cluster_label"] == i:
                feature["properties"]["colour"] = colours[colour_idx % len(colours)]
                colour_idx += 1

    # Write the feature collection to the GeoJSON file
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
            if i[2] > max_elevation:
                max_elevation = i[2]

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

def save_tracks_as_gpx(feature_collection):
    """Converts a GeoJSON FeatureCollection of tracks to individual GPX files and saves them."""

    # Create the output directory if it doesn't exist
    output_dir = os.path.join(os.getcwd(), "track_downloads")
    os.makedirs(output_dir, exist_ok=True)

    for feature in feature_collection["features"]:
        track_name = feature["properties"]["name"].replace(" ", "").replace("@", "_")
        gpx_data = feature["geometry"]["coordinates"]

        # Create a new GPX XML document
        gpx = Element("gpx", attrib={"version": "1.1", "creator": "Your Creator Name"})
        trk = SubElement(gpx, "trk")
        trkseg = SubElement(trk, "trkseg")

        for point in gpx_data:
            trkpt = SubElement(
                trkseg, "trkpt", attrib={"lat": str(point[1]), "lon": str(point[0])}
            )
            ele = SubElement(trkpt, "ele")
            ele.text = str(point[2])  # Elevation
            # You can add more track point attributes here if needed

        # Save the GPX file
        gpx_filename = os.path.join(output_dir, f"{track_name}.gpx")
        with open(gpx_filename, "w", encoding="utf-8") as f:
            f.write(tostring(gpx, encoding="unicode"))

    print("GPX files created")

def split_features_to_files(features, output_dir):
    """Splits GeoJSON features into individual files and generates a manifest and marker features."""

    manifest = []
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

        with open(filepath, "w", encoding="utf-8") as out_f:
            json.dump(feature_geojson, out_f, ensure_ascii=False, indent=2)

        # Add relative path to manifest
        manifest.append(os.path.join(output_dir, filename).replace("\\", "/"))

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
    with open(output_path, "w", encoding="utf-8") as mf:
        json.dump(track_markers_geojson, mf, ensure_ascii=False, indent=2)

def create_tracks_content_page(years, feature_collection):
    """Create an HTML page with track data to be embedded in an iframe."""

    # HTML template for the start of the tracks content page
    html_start = """\
<!DOCTYPE html>
<html>
    <head>
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

            /* Define the grid for larger screens (e.g., desktop) */
            @media (min-width: 1501px) {
                .track-list {
                    grid-template-columns: repeat(5, 1fr);
                }
            }

            /* Define the grid for medium-sized screens */
            @media (min-width: 1291px) and (max-width: 1500px) {
                .track-list {
                    grid-template-columns: repeat(4, 1fr);
                }
            }

            /* Define the grid for medium-sized screens */
            @media (min-width: 1081px) and (max-width: 1290px) {
                .track-list {
                    grid-template-columns: repeat(3, 1fr);
                }
            }

            /* Define the grid for smaller screens (e.g., mobile) */
            @media (max-width: 1080px) {
                .track-list {
                    grid-template-columns: repeat(2, 1fr);
                }
            }

        .track {
            font-size: 1.0em;
            border: 10px solid #ccc;
            border-radius: 10px;
            padding: 10px;
            text-align: center;
        }

        .track img {
            max-width: 100%; /* Set maximum width to fit the container */
            max-height: 100%; /* Set maximum height to fit the container */
            display: block; /* Ensures images resize properly */
            margin: auto; /* Centers the images horizontally */
        }
        
        .track-details {
            text-align: left;
            padding-left: 20px;
            line-height: 0.9;
        }
        
        .track-details-distance {
            line-height: 0.6;
        }

        .track-details-ascent-normal {
            font-weight: normal;
        }
        
        .track-details-ascent-bold {
            font-weight: bold;
        }

        /* Styling for the modal */
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.7);
            justify-content: center;
            align-items: center;
        }

        /* Styling for the larger image */
        .modal-content {
            display: block;
            max-width: 80%;
            max-height: 80%;
        }
        
        /* Styling for the clickable images */
        .clickable-image {
            cursor: pointer;
        }

        /* Custom styles for larger sliders */
        input[type="range"] {
            width: 400px; /* Adjust the width */
        }
        
        a {
            display: block;
            padding: 0.5em 1em;
            background-color: #0A4478;
            color: white;
            text-decoration: none;
            border-radius: 5px;
            transition: background-color 0.2s ease;
            font-size: 1.0em;
        }
        a:hover {
            background-color: #1A4E87;
        }
    </style>
    <script>
        function displayLargeImage(imageUrl) {
            var modal = document.getElementById('modal');
            var largerImg = document.getElementById('largerImage');
            // Show the modal
            modal.style.display = 'flex';
            // Set the larger image source
            largerImg.src = imageUrl;
        }

        function closeModal() {
            var modal = document.getElementById('modal');
            modal.style.display = 'none';
        }
    </script>
    </head>
    <body>
        <!-- Modal for displaying larger image -->
        <div id="modal" class="modal" onclick="closeModal()">
            <img id="largerImage" class="modal-content" src="" alt="Larger Image">
        </div>
        
        <!-- Container for distance sliders -->
        <div style="display: flex; justify-content: center; align-items: center; flex-wrap: wrap;">
        <div style="margin: 10px;">
            <label for="minDistance">Minimum Distance:</label>
            <input type="range" id="minDistance" name="minDistance" min="0" max="15" value="0">
            <span id="minDistanceValue"></span> miles <!-- Display selected value -->
            <br><br>
            <label for="maxDistance">Maximum Distance:</label>
            <input type="range" id="maxDistance" name="maxDistance" min="0" max="15" value="15">
            <span id="maxDistanceValue"></span> miles <!-- Display selected value -->
            <br><br><br>
            <label for="minAscent">Minimum Ascent:</label>
            <input type="range" id="minAscent" name="minAscent" min="0" max="1500" value="0">
            <span id="minAscentValue"></span> meters <!-- Display selected value -->
            <br><br>
            <label for="maxAscent">Maximum Ascent:</label>
            <input type="range" id="maxAscent" name="maxAscent" min="0" max="1500" value="1500">
            <span id="maxAscentValue"></span> meters <!-- Display selected value -->
            <br><br>
        </div>
        </div>
    """

    # HTML template for the end of the tracks content page
    html_end = """    
    </body>
<script>
  // Function to set maximum values for distance and ascent sliders
  function setMaxSliderValues() {
    // Retrieve all tracks
    var tracks = document.querySelectorAll('.track');

    // Initialize variables to store maximum distance and ascent values
    var maxDistance = 0;
    var maxAscent = 0;

    // Loop through each track to find the maximum distance and ascent
    tracks.forEach(function(track) {
      // Extract distance and ascent details from track elements
      var trackDistance = parseFloat(track.querySelector('.track-details-distance').innerText.trim().split(' ')[0]);
      var ascentDetails = track.querySelector('.track-details-ascent-normal') || track.querySelector('.track-details-ascent-bold');
      var trackAscent = parseFloat(ascentDetails.innerText.trim().split(' ')[1]);

      // Update the maximum distance and ascent values
      maxDistance = Math.max(maxDistance, Math.ceil(trackDistance)); // Round up distance to nearest whole number
      maxAscent = Math.max(maxAscent, Math.ceil(trackAscent / 100) * 100); // Round up ascent to nearest 100
    });

    // Set the maximum values for distance and ascent sliders
    document.getElementById('maxDistance').setAttribute('max', maxDistance);
    document.getElementById('maxAscent').setAttribute('max', maxAscent);
    
    // Set the slider values to the maximum
    document.getElementById('maxDistance').value = maxDistance;
    document.getElementById('maxAscent').value = maxAscent;

    // Display the maximum values next to the sliders
    document.getElementById('maxDistanceValue').innerText = maxDistance;
    document.getElementById('maxAscentValue').innerText = maxAscent;
  }

  // Function to filter tracks based on slider values
  function filterTracks() {
    // Retrieve slider values for minimum and maximum distance and ascent
    var minDistance = parseFloat(document.getElementById('minDistance').value);
    var maxDistance = parseFloat(document.getElementById('maxDistance').value);
    var minAscent = parseFloat(document.getElementById('minAscent').value);
    var maxAscent = parseFloat(document.getElementById('maxAscent').value);

    // Retrieve all tracks
    var tracks = document.querySelectorAll('.track');

    // Loop through each track and show/hide tracks based on distance and ascent criteria
    tracks.forEach(function(track) {
      var trackDistance = parseFloat(track.querySelector('.track-details-distance').innerText.trim().split(' ')[0]);
      var ascentDetails = track.querySelector('.track-details-ascent-normal') || track.querySelector('.track-details-ascent-bold');
      var trackAscent = parseFloat(ascentDetails.innerText.trim().split(' ')[1]);

      // Show the track if it satisfies the distance and ascent criteria, otherwise hide it
      if (
        trackDistance >= minDistance &&
        trackDistance <= maxDistance &&
        trackAscent >= minAscent &&
        trackAscent <= maxAscent
      ) {
        track.style.display = 'block';
      } else {
        track.style.display = 'none';
      }
    });

    // Display the selected slider values
    document.getElementById('minDistanceValue').innerText = minDistance;
    document.getElementById('maxDistanceValue').innerText = maxDistance;
    document.getElementById('minAscentValue').innerText = minAscent;
    document.getElementById('maxAscentValue').innerText = maxAscent;
  }

  // Add event listeners to the sliders to trigger filtering when their values change
  document.getElementById('minDistance').addEventListener('input', filterTracks);
  document.getElementById('maxDistance').addEventListener('input', filterTracks);
  document.getElementById('minAscent').addEventListener('input', filterTracks);
  document.getElementById('maxAscent').addEventListener('input', filterTracks);

  // Initialize maximum slider values and perform initial filtering
  setMaxSliderValues();
  filterTracks();
  
  // Send height to parent window for iframe resizing
  function sendHeight() {
      const height = document.body.scrollHeight;
      window.parent.postMessage({ type: 'resize', height: height }, '*');
  }
  
  // Send height on load and when window is resized
  window.addEventListener('load', sendHeight);
  window.addEventListener('resize', sendHeight);
  
  // Also send height after a short delay to account for dynamic content
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
<div class="track" style="border: 10px solid {grid_colour};">
                <div class="track-details">
                    <br>{feature['properties']['gridref']}</br>
                    <br>Date: {date_title}</br>
                    <br>Distance:</br>
                    <div class="track-details-distance">
                        <br>{feature['properties']['distance_mi']} miles</br>
                        <br>{feature['properties']['distance_km']} km</br>
                    </div>
                    <br>Duration: {feature['properties']['duration']}</br>
                    <div class="track-details-ascent-{asc_desc_font_weight}">
                        <br>Ascent: {feature['properties']['ascent']}m</br>
                        <br>Descent: {feature['properties']['descent']}m</br>
                    </div>
                </div>
                <img src="{feature['properties']['elevation_profile_link']}" alt="Elevation Profile" class="clickable-image" onclick="displayLargeImage('{feature['properties']['elevation_profile_link']}')">
                <a href=\"{feature['properties']['ind_map_link_os']}" target=\"_blank\" style='display: block; margin-top: 5px;'>Open OS Map</a>
                <a href=\"{feature['properties']['ind_map_link']}" target=\"_blank\" style='display: block; margin-top: 5px;'>Open Standard Map</a>
                <a href=\"{feature['properties']['googleMapsLink']}\" target=\"_blank\" style='display: block; margin-top: 5px;'>Starting Location on Google Maps</a>
                <a href=\"{feature['properties']['download_link']}\" download=\"{os.path.basename(feature['properties']['download_link'])}\" style='display: block; margin-top: 5px;'>Download GPX Track File</a>
                <div style='text-align: center; margin-top: 10px; font-weight: bold;'>{feature['properties']['place_name']}</div>
            </div>
                    """
                        f.write(html_track)

                # Close the track-list section for the current year
                f.write("</div>")

        # Write the end of the HTML page
        f.write(html_end)

    print("Tracks content page created")

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

    # Create individual gpx files from the created data for users to download
    save_tracks_as_gpx(feature_collection)

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
    
    # Create the tracks content page
    create_tracks_content_page(years, feature_collection)

if __name__ == "__main__":
    main()
