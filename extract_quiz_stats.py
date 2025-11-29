"""
Moorwalkers Quiz Statistics Extractor
Extract various statistics from moorwalkers.geojson for quiz questions
"""

import json
from datetime import datetime
from collections import Counter, defaultdict
import statistics

# ===== DATE FILTER CONFIGURATION =====
# Set these dates to filter walks within a specific date range
# Format: 'YYYY-MM-DD' or None to disable filtering
START_DATE = "2025-01-22"  # e.g., '2024-01-01' or None for no start limit
END_DATE = None    # e.g., '2025-12-31' or None for no end limit
# ======================================

def load_geojson(filepath='moorwalkers.geojson'):
    """Load the GeoJSON data"""
    with open(filepath, 'r') as f:
        return json.load(f)

def filter_by_date(features):
    """Filter features based on START_DATE and END_DATE configuration"""
    if START_DATE is None and END_DATE is None:
        return features
    
    filtered = []
    start_dt = datetime.fromisoformat(START_DATE) if START_DATE else None
    end_dt = datetime.fromisoformat(END_DATE) if END_DATE else None
    
    for feature in features:
        walk_date = datetime.fromisoformat(feature['properties']['date'])
        
        # Check if within date range
        if start_dt and walk_date < start_dt:
            continue
        if end_dt and walk_date > end_dt:
            continue
            
        filtered.append(feature)
    
    return filtered

def extract_quiz_stats(data):
    """Extract comprehensive statistics for quiz questions"""
    
    features = data['features']
    
    # Apply date filtering
    original_count = len(features)
    features = filter_by_date(features)
    filtered_count = len(features)
    
    if filtered_count == 0:
        print("WARNING: No walks found in the specified date range!")
        return None
    
    stats = {}
    
    # Add filtering info
    stats['filter_info'] = {
        'start_date': START_DATE,
        'end_date': END_DATE,
        'original_walk_count': original_count,
        'filtered_walk_count': filtered_count,
        'walks_excluded': original_count - filtered_count
    }
    
    # === BASIC COUNTS ===
    stats['total_walks'] = len(features)
    
    # === DISTANCE STATISTICS ===
    distances_km = [f['properties']['distance_km'] for f in features]
    distances_mi = [f['properties']['distance_mi'] for f in features]
    
    stats['distance_stats_km'] = {
        'total': round(sum(distances_km), 2),
        'average': round(statistics.mean(distances_km), 2),
        'median': round(statistics.median(distances_km), 2),
        'longest': round(max(distances_km), 2),
        'shortest': round(min(distances_km), 2),
        'std_dev': round(statistics.stdev(distances_km), 2)
    }
    
    stats['distance_stats_miles'] = {
        'total': round(sum(distances_mi), 2),
        'average': round(statistics.mean(distances_mi), 2),
        'median': round(statistics.median(distances_mi), 2),
        'longest': round(max(distances_mi), 2),
        'shortest': round(min(distances_mi), 2)
    }
    
    # === ELEVATION STATISTICS ===
    ascents = [f['properties']['ascent'] for f in features]
    descents = [abs(f['properties']['descent']) for f in features]
    
    stats['elevation_stats'] = {
        'total_ascent': sum(ascents),
        'total_descent': sum(descents),
        'average_ascent': round(statistics.mean(ascents), 2),
        'max_ascent': max(ascents),
        'min_ascent': min(ascents),
        'average_descent': round(statistics.mean(descents), 2),
        'max_descent': max(descents),
        'min_descent': min(descents)
    }
    
    # === DURATION STATISTICS ===
    durations = []
    for f in features:
        duration_str = f['properties']['duration']
        parts = duration_str.split(':')
        hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
        total_minutes = hours * 60 + minutes + seconds / 60
        durations.append(total_minutes)
    
    stats['duration_stats'] = {
        'total_hours': round(sum(durations) / 60, 2),
        'average_minutes': round(statistics.mean(durations), 2),
        'average_hours': round(statistics.mean(durations) / 60, 2),
        'longest_minutes': round(max(durations), 2),
        'longest_hours': round(max(durations) / 60, 2),
        'shortest_minutes': round(min(durations), 2),
        'shortest_hours': round(min(durations) / 60, 2)
    }
    
    # === DATE STATISTICS ===
    dates = [datetime.fromisoformat(f['properties']['date']) for f in features]
    dates.sort()
    
    stats['date_stats'] = {
        'first_walk': dates[0].strftime('%Y-%m-%d'),
        'most_recent_walk': dates[-1].strftime('%Y-%m-%d'),
        'days_between_first_and_last': (dates[-1] - dates[0]).days
    }
    
    # === LOCATION STATISTICS ===
    place_names = [f['properties'].get('place_name', 'Unknown') for f in features]
    place_counter = Counter(place_names)
    stats['most_common_places'] = dict(place_counter.most_common(10))
    stats['unique_places'] = len(set(place_names))
    
    # Grid references
    gridrefs = [f['properties'].get('gridref', '') for f in features]
    stats['unique_grid_refs'] = len(set(gridrefs))
    
    # === CLUSTER STATISTICS ===
    clusters = [f['properties'].get('cluster_label') for f in features if 'cluster_label' in f['properties']]
    if clusters:
        cluster_counter = Counter(clusters)
        stats['cluster_stats'] = {
            'unique_clusters': len(set(clusters)),
            'most_common_cluster': cluster_counter.most_common(1)[0][0] if cluster_counter else None,
            'walks_per_cluster': dict(cluster_counter.most_common())
        }
    
    # === COORDINATE STATISTICS ===
    center_lats = [f['properties']['center_lat'] for f in features]
    center_lons = [f['properties']['center_lon'] for f in features]
    
    stats['coordinate_stats'] = {
        'northernmost_lat': max(center_lats),
        'southernmost_lat': min(center_lats),
        'easternmost_lon': max(center_lons),
        'westernmost_lon': min(center_lons),
        'average_lat': round(statistics.mean(center_lats), 6),
        'average_lon': round(statistics.mean(center_lons), 6)
    }
    
    # === SPECIFIC WALK RECORDS ===
    longest_walk = max(features, key=lambda x: x['properties']['distance_km'])
    shortest_walk = min(features, key=lambda x: x['properties']['distance_km'])
    highest_ascent = max(features, key=lambda x: x['properties']['ascent'])
    longest_duration = max(features, key=lambda x: x['properties']['duration'])
    
    stats['record_walks'] = {
        'longest': {
            'name': longest_walk['properties']['name'],
            'distance_km': longest_walk['properties']['distance_km'],
            'date': longest_walk['properties']['date']
        },
        'shortest': {
            'name': shortest_walk['properties']['name'],
            'distance_km': shortest_walk['properties']['distance_km'],
            'date': shortest_walk['properties']['date']
        },
        'highest_ascent': {
            'name': highest_ascent['properties']['name'],
            'ascent': highest_ascent['properties']['ascent'],
            'date': highest_ascent['properties']['date']
        },
        'longest_duration': {
            'name': longest_duration['properties']['name'],
            'duration': longest_duration['properties']['duration'],
            'date': longest_duration['properties']['date']
        }
    }
    
    # === PACE STATISTICS ===
    paces = []  # minutes per km
    for f in features:
        duration_str = f['properties']['duration']
        parts = duration_str.split(':')
        hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
        total_minutes = hours * 60 + minutes + seconds / 60
        distance = f['properties']['distance_km']
        if distance > 0:
            pace = total_minutes / distance
            paces.append(pace)
    
    stats['pace_stats'] = {
        'average_min_per_km': round(statistics.mean(paces), 2),
        'fastest_min_per_km': round(min(paces), 2),
        'slowest_min_per_km': round(max(paces), 2)
    }
    
    # === COLOUR STATISTICS ===
    colours = [f['properties'].get('colour', 'Unknown') for f in features]
    colour_counter = Counter(colours)
    stats['colour_distribution'] = dict(colour_counter.most_common())
    
    # === MONTHLY DISTANCE TOTALS ===
    monthly_distances = defaultdict(float)
    for f in features:
        date = datetime.fromisoformat(f['properties']['date'])
        month_key = date.strftime('%Y-%m')
        monthly_distances[month_key] += f['properties']['distance_km']
    
    stats['monthly_distance_totals'] = dict(sorted(monthly_distances.items()))
    stats['busiest_month'] = max(monthly_distances.items(), key=lambda x: x[1]) if monthly_distances else None
    
    # === COORDINATE POINTS STATISTICS ===
    total_points = 0
    max_points = 0
    min_points = float('inf')
    
    for f in features:
        points = len(f['geometry']['coordinates'])
        total_points += points
        max_points = max(max_points, points)
        min_points = min(min_points, points)
    
    stats['coordinate_points'] = {
        'total_points': total_points,
        'average_points_per_walk': round(total_points / len(features), 2),
        'max_points_in_walk': max_points,
        'min_points_in_walk': min_points
    }
    
    return stats

def print_quiz_questions(stats):
    """Generate sample quiz questions based on the statistics"""
    
    print("\n" + "="*80)
    print("SAMPLE QUIZ QUESTIONS FROM MOORWALKERS DATA")
    print("="*80)
    
    # Show filter info if dates are set
    if START_DATE or END_DATE:
        print("\n--- DATE FILTER APPLIED ---")
        print(f"Start Date: {START_DATE or 'No limit'}")
        print(f"End Date: {END_DATE or 'No limit'}")
        print(f"Walks included: {stats['filter_info']['filtered_walk_count']} of {stats['filter_info']['original_walk_count']}")
        print(f"Walks excluded: {stats['filter_info']['walks_excluded']}")
        print("="*80)
    
    print("\n--- BASIC STATISTICS ---")
    print(f"Q: How many walks have been recorded in total?")
    print(f"A: {stats['total_walks']}")
    
    print(f"\nQ: What is the total distance walked in kilometers?")
    print(f"A: {stats['distance_stats_km']['total']} km")
    
    print(f"\nQ: What is the total distance walked in miles?")
    print(f"A: {stats['distance_stats_miles']['total']} miles")
    
    print(f"\nQ: What is the average walk distance?")
    print(f"A: {stats['distance_stats_km']['average']} km ({stats['distance_stats_miles']['average']} miles)")
    
    print("\n--- EXTREME WALKS ---")
    print(f"Q: What was the longest walk ever recorded?")
    print(f"A: {stats['record_walks']['longest']['distance_km']} km on {stats['record_walks']['longest']['date']}")
    
    print(f"\nQ: What was the shortest walk?")
    print(f"A: {stats['record_walks']['shortest']['distance_km']} km on {stats['record_walks']['shortest']['date']}")
    
    print(f"\nQ: What walk had the highest ascent?")
    print(f"A: {stats['record_walks']['highest_ascent']['ascent']} meters on {stats['record_walks']['highest_ascent']['date']}")
    
    print(f"\nQ: What was the longest walk by duration?")
    print(f"A: {stats['record_walks']['longest_duration']['duration']} on {stats['record_walks']['longest_duration']['date']}")
    
    print("\n--- ELEVATION ---")
    print(f"Q: What is the total elevation gained across all walks?")
    print(f"A: {stats['elevation_stats']['total_ascent']} meters")
    
    print(f"\nQ: What is the average elevation gain per walk?")
    print(f"A: {stats['elevation_stats']['average_ascent']} meters")
    
    print(f"\nQ: What is the maximum elevation gain in a single walk?")
    print(f"A: {stats['elevation_stats']['max_ascent']} meters")
    
    print("\n--- TIME STATISTICS ---")
    print(f"Q: What is the total time spent walking?")
    print(f"A: {stats['duration_stats']['total_hours']} hours")
    
    print(f"\nQ: What is the average walk duration?")
    print(f"A: {stats['duration_stats']['average_hours']} hours")
    
    print(f"\nQ: When was the first walk recorded?")
    print(f"A: {stats['date_stats']['first_walk']}")
    
    print(f"\nQ: When was the most recent walk?")
    print(f"A: {stats['date_stats']['most_recent_walk']}")
    
    print("\n--- LOCATION ---")
    print(f"Q: How many unique places have been visited?")
    print(f"A: {stats['unique_places']}")
    
    print(f"\nQ: What is the most frequently visited place?")
    most_common_place = list(stats['most_common_places'].items())[0]
    print(f"A: {most_common_place[0]} ({most_common_place[1]} times)")
    
    print("\n--- PACE ---")
    print(f"Q: What is the average walking pace?")
    print(f"A: {stats['pace_stats']['average_min_per_km']} minutes per kilometer")
    
    print(f"\nQ: What was the fastest pace recorded?")
    print(f"A: {stats['pace_stats']['fastest_min_per_km']} minutes per kilometer")
    
    print("\n" + "="*80)

def save_stats_to_file(stats, filename='quiz_stats.json'):
    """Save all statistics to a JSON file"""
    with open(filename, 'w') as f:
        json.dump(stats, f, indent=2)
    print(f"\nAll statistics saved to {filename}")

def main():
    print("Loading moorwalkers data...")
    data = load_geojson()
    
    print("Extracting statistics...")
    if START_DATE or END_DATE:
        print(f"Filtering walks between {START_DATE or 'beginning'} and {END_DATE or 'present'}...")
    
    stats = extract_quiz_stats(data)
    
    if stats is None:
        print("No data to process. Exiting.")
        return
    
    # Print quiz questions
    print_quiz_questions(stats)
    
    # Save to file
    save_stats_to_file(stats)
    
    # Also print all raw stats
    print("\n" + "="*80)
    print("COMPLETE STATISTICS (for reference)")
    print("="*80)
    print(json.dumps(stats, indent=2))

if __name__ == '__main__':
    main()
