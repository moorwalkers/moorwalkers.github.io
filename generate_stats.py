
import json
import pandas as pd

# Load the full JSON dataset
with open('moorwalkers.geojson') as f:
    data = json.load(f)

# Extract properties from each Feature entry
rows = []
for feat in data['features']:
    p = feat['properties']
    rows.append(p)

# Convert to DataFrame
df = pd.DataFrame(rows)

# Save to Excel
df.to_excel('generated_stats.xlsx', index=False)
