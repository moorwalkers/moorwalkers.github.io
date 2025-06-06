import xml.etree.ElementTree as ET
from datetime import datetime
import os

def convert_gpx(input_path, output_path):
    """Convert OS Maps GPX files to standard GPX format."""
    ns = {
        'default': 'http://www.topografix.com/GPX/1/1',
        'os': 'https://ordnancesurvey.co.uk/public/schema/route/0.1'
    }

    tree = ET.parse(input_path)
    root = tree.getroot()

    # Extract metadata
    metadata = root.find('default:metadata', ns)
    time_elem = metadata.find('default:time', ns) if metadata is not None else None
    time_text = time_elem.text if time_elem is not None else datetime.utcnow().isoformat() + 'Z'

    # Create new GPX root
    gpx = ET.Element('gpx', {
        'version': '1.0',
        'creator': 'OS Maps Shared to Standard Converter',
        'xmlns': 'http://www.topografix.com/GPX/1/0',
        'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
        'xsi:schemaLocation': 'http://www.topografix.com/GPX/1/0 http://www.topografix.com/GPX/1/0/gpx.xsd'
    })

    ET.SubElement(gpx, 'time').text = time_text
    trk = ET.SubElement(gpx, 'trk')
    ET.SubElement(trk, 'name').text = f"{time_text[:10]} @ {time_text[11:19].replace(':', '-')}"
    trkseg = ET.SubElement(trk, 'trkseg')

    # Extract track points
    for trkpt in root.findall('.//default:trkpt', ns):
        lat = trkpt.attrib['lat']
        lon = trkpt.attrib['lon']
        new_trkpt = ET.SubElement(trkseg, 'trkpt', lat=lat, lon=lon)
        ET.SubElement(new_trkpt, 'ele').text = '0'  # Placeholder
        ET.SubElement(new_trkpt, 'time').text = time_text  # Placeholder
        ET.SubElement(new_trkpt, 'speed').text = '0.0'  # Placeholder

    # Write to output file
    tree = ET.ElementTree(gpx)
    # Write with pretty printing (indenting)
    import xml.dom.minidom
    xml_str = ET.tostring(gpx, encoding='utf-8')
    parsed = xml.dom.minidom.parseString(xml_str)
    pretty_xml_as_str = parsed.toprettyxml(indent="  ")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(pretty_xml_as_str)

def main():
    """Main function to process GPX files."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_folder = os.path.join(script_dir, "original")
    os.makedirs(input_folder, exist_ok=True)
    output_folder = os.path.join(script_dir, "converted")
    os.makedirs(output_folder, exist_ok=True)

    for filename in os.listdir(input_folder):
        if filename.lower().endswith('.gpx'):
            input_path = os.path.join(input_folder, filename)
            print(f"Processing {input_path}")
            while True:
                date_input = input("Enter the date for the output file (yyyy-mm-dd): ").strip()
                try:
                    datetime_obj = datetime.strptime(date_input, "%Y-%m-%d")
                    break
                except ValueError:
                    print("Invalid date format. Please use yyyy-mm-dd.")

            output_filename = f"{date_input} @ 18-00-00.gpx"
            output_path = os.path.join(output_folder, output_filename)
            convert_gpx(input_path, output_path)
            print(f"Converted GPX file saved to {output_path}")

if __name__ == "__main__":
                main()