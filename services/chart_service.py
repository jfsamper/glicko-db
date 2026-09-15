"""Pure rating-chart data and path construction helpers."""
from datetime import datetime


def build_smooth_path(points):
    if not points:
        return ""
    if len(points) == 1:
        return f"M {points[0]['x']:.2f},{points[0]['y']:.2f}"
    if len(points) == 2:
        return f"M {points[0]['x']:.2f},{points[0]['y']:.2f} L {points[1]['x']:.2f},{points[1]['y']:.2f}"

    parts = [f"M {points[0]['x']:.2f},{points[0]['y']:.2f}"]
    for index in range(1, len(points)):
        previous = points[index - 1]
        current = points[index]
        if index == len(points) - 1:
            control_one_x = previous["x"] + \
                (current["x"] - previous["x"]) * 0.5
            control_one_y = previous["y"]
            control_two_x = current["x"] - (current["x"] - previous["x"]) * 0.5
            control_two_y = current["y"]
        else:
            next_point = points[index + 1]
            control_one_x = previous["x"] + \
                (current["x"] - previous["x"]) * 0.5
            control_one_y = previous["y"]
            control_two_x = current["x"] - \
                (next_point["x"] - previous["x"]) * 0.25
            control_two_y = current["y"]
        parts.append(
            f"C {control_one_x:.2f},{control_one_y:.2f} "
            f"{control_two_x:.2f},{control_two_y:.2f} "
            f"{current['x']:.2f},{current['y']:.2f}"
        )
    return " ".join(parts)


def build_rating_chart_data(snapshots):
    if not snapshots:
        return {
            "points": [],
            "axis_labels": [],
            "polyline": "",
            "path": "",
            "baseline_y": 0,
            "min_rating": 0,
            "max_rating": 0,
            "label_min": 0,
            "label_max": 0,
        }

    ratings = [row["rating"] for row in snapshots]
    initial_rating = snapshots[0]["rating"]
    raw_min = min(ratings)
    raw_max = max(ratings)
    span = raw_max - raw_min
    padding_rating = 20 if span == 0 else max(10, span * 0.10)
    min_rating = raw_min - padding_rating
    max_rating = raw_max + padding_rating
    span = max_rating - min_rating
    padding = 24
    width = 620
    height = 220
    points = []

    for index, row in enumerate(snapshots):
        if len(snapshots) == 1:
            x = width / 2
        else:
            x = padding + (index / (len(snapshots) - 1)) * \
                (width - padding * 2)
        value = row["rating"]
        y = height - padding - ((value - min_rating) /
                                span) * (height - padding * 2)
        points.append(
            {
                "x": round(x, 2),
                "y": round(y, 2),
                "date": row["snapshot_date"],
                "rating": round(value, 1),
            }
        )

    baseline_y = height - padding - \
        ((initial_rating - min_rating) / span) * (height - padding * 2)
    label_min = round(min_rating)
    label_max = round(max_rating)
    month_names = ("jan", "feb", "mar", "apr", "may", "jun",
                   "jul", "aug", "sep", "oct", "nov", "dec")
    label_indexes = []
    for position in range(min(5, len(points))):
        index = round(position * (len(points) - 1) /
                      max(1, min(5, len(points)) - 1))
        if index not in label_indexes:
            label_indexes.append(index)
    axis_labels = []
    for index in label_indexes:
        point = points[index]
        snapshot_date = datetime.strptime(str(point["date"]), "%Y-%m-%d")
        axis_labels.append(
            {
                "x": point["x"],
                "label": f"{month_names[snapshot_date.month - 1]}/{snapshot_date.strftime('%y')}",
                "anchor": "start" if index == 0 else "end" if index == len(points) - 1 else "middle",
            }
        )

    return {
        "points": points,
        "axis_labels": axis_labels,
        "polyline": " ".join(f"{point['x']},{point['y']}" for point in points),
        "path": build_smooth_path(points),
        "baseline_y": round(baseline_y, 2),
        "min_rating": round(min_rating, 1),
        "max_rating": round(max_rating, 1),
        "label_min": label_min,
        "label_max": label_max,
        "baseline_rating": round(initial_rating, 1),
    }
