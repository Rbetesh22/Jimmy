"""Apple Health XML export ingester."""
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from .base import Document, _h


# Key health metrics to extract
TRACKED_TYPES = {
    "HKQuantityTypeIdentifierStepCount": "steps",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "walking_distance_km",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_calories",
    "HKQuantityTypeIdentifierHeartRate": "heart_rate_bpm",
    "HKQuantityTypeIdentifierBodyMass": "weight",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_heart_rate",
    "HKQuantityTypeIdentifierVO2Max": "vo2_max",
    "HKQuantityTypeIdentifierFlightsClimbed": "flights_climbed",
    "HKCategoryTypeIdentifierSleepAnalysis": "sleep",
}


class AppleHealthIngester:
    def ingest(self, path: str) -> list[Document]:
        """Parse Apple Health export.xml.

        Groups data by date and creates daily health summaries.
        """
        p = Path(path)
        if p.is_dir():
            export_file = p / "apple_health_export" / "export.xml"
            if not export_file.exists():
                export_file = p / "export.xml"
            if not export_file.exists():
                raise FileNotFoundError("export.xml not found")
            p = export_file

        docs = []
        daily_data: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

        # Use iterparse for memory efficiency on large files
        context = ET.iterparse(str(p), events=("end",))
        workout_docs = []

        for event, elem in context:
            if elem.tag == "Record":
                rec_type = elem.get("type", "")
                if rec_type in TRACKED_TYPES:
                    metric = TRACKED_TYPES[rec_type]
                    date = (elem.get("startDate", "") or "")[:10]
                    value = elem.get("value", "")
                    if date and value:
                        try:
                            daily_data[date][metric].append(float(value))
                        except ValueError:
                            pass
                elem.clear()

            elif elem.tag == "Workout":
                workout_type = elem.get("workoutActivityType", "").replace("HKWorkoutActivityType", "")
                duration = elem.get("duration", "")
                calories = elem.get("totalEnergyBurned", "")
                distance = elem.get("totalDistance", "")
                date = (elem.get("startDate", "") or "")[:10]

                if workout_type and date:
                    content = f"Workout: {workout_type}"
                    if duration:
                        try:
                            mins = round(float(duration))
                            content += f"\nDuration: {mins} minutes"
                        except ValueError:
                            pass
                    if calories:
                        content += f"\nCalories: {calories}"
                    if distance:
                        try:
                            km = round(float(distance), 2)
                            content += f"\nDistance: {km} km"
                        except ValueError:
                            pass
                    content += f"\nDate: {date}"

                    workout_docs.append(Document(
                        id=f"health_workout_{_h(workout_type + date + str(duration))}",
                        content=content,
                        source="apple_health",
                        title=f"Workout: {workout_type} ({date})",
                        metadata={"type": "workout", "date": date, "activity": workout_type},
                    ))
                elem.clear()

        # Create daily summary documents
        for date in sorted(daily_data.keys(), reverse=True)[:365]:  # Last year
            metrics = daily_data[date]
            lines = [f"Health Summary: {date}"]

            if "steps" in metrics:
                lines.append(f"Steps: {int(sum(metrics['steps'])):,}")
            if "walking_distance_km" in metrics:
                lines.append(f"Walking distance: {sum(metrics['walking_distance_km']):.1f} km")
            if "active_calories" in metrics:
                lines.append(f"Active calories: {int(sum(metrics['active_calories'])):,}")
            if "flights_climbed" in metrics:
                lines.append(f"Flights climbed: {int(sum(metrics['flights_climbed']))}")
            if "heart_rate_bpm" in metrics:
                vals = metrics["heart_rate_bpm"]
                lines.append(f"Heart rate: avg {sum(vals)/len(vals):.0f} bpm (range {min(vals):.0f}-{max(vals):.0f})")
            if "resting_heart_rate" in metrics:
                vals = metrics["resting_heart_rate"]
                lines.append(f"Resting heart rate: {sum(vals)/len(vals):.0f} bpm")
            if "weight" in metrics:
                vals = metrics["weight"]
                lines.append(f"Weight: {vals[-1]:.1f} kg")
            if "sleep" in metrics:
                total = sum(metrics["sleep"])
                lines.append(f"Sleep: {total:.1f} hours")

            content = "\n".join(lines)
            docs.append(Document(
                id=f"health_daily_{_h(date)}",
                content=content,
                source="apple_health",
                title=f"Health: {date}",
                metadata={"type": "daily_summary", "date": date},
            ))

        docs.extend(workout_docs[:500])  # Cap workouts
        return docs
