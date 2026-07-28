import gpxpy

with open("../data/raw/gpx/IATF26_K25_260429.gpx", "r") as file:
    gpx = gpxpy.parse(file)

points = []

for track in gpx.tracks:
    for segment in track.segments:
        for point in segment.points:
            points.append((point.longitude, point.latitude))