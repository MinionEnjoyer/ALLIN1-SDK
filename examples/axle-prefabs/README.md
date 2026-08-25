# Axle prefab acceptance fixtures

These fixtures exercise the same catalog and resolver used by the Vehicle
Workbench:

- `three-axle-bus.json`: 6x2 rear-steer behavior with dual middle drive and a
  single rear tag.
- `four-axle-heavy-truck.json`: 8x4 twin steering with two driven tandem axles.
- `five-axle-crane.json`: 10x4 multi-steer behavior across all five canonical
  GTA axle pairs.

Each file records vehicle-local bone positions, the target, export mode, and
the game-reported physics wheel count. They intentionally contain no authored
runtime wheel indices; the target resolver must produce and validate those.
