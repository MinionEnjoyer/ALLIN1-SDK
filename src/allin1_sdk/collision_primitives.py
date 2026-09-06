"""Collision display geometry following pinned CodeWalker Bounds.cs conventions.

Curves are tessellated for display, not claimed to be physics contact meshes.
"""
import math


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def mul(a, amount):
    return tuple(x * amount for x in a)


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def length(a):
    return math.sqrt(sum(x*x for x in a))


def unit(a):
    size = length(a)
    if size < 1e-10:
        raise ValueError("Collision primitive has a degenerate axis")
    return mul(a, 1 / size)


def transform(points, owner):
    matrices = []
    while owner is not None:
        element = owner.find("./CompositeTransform")
        if element is not None:
            values = [float(value) for value in (element.text or "").replace(",", " ").split()]
            if len(values) != 16 or not all(math.isfinite(value) for value in values):
                raise ValueError("Collision composite transform must contain 16 finite values")
            if any(abs(values[index]) > 1e-6 for index in (3, 7, 11)) or abs(values[15] - 1) > 1e-6:
                raise ValueError("Collision composite transform is not affine")
            matrices.append(values)
        owner = owner.getparent()
    result = []
    for original in points:
        point = original
        for matrix in matrices:
            # SharpDX row-vector convention: translation is M41/M42/M43.
            point = tuple(sum(point[j] * matrix[j*4+i] for j in range(3)) + matrix[12+i] for i in range(3))
        if not all(math.isfinite(value) and abs(value) <= 1e9 for value in point):
            raise ValueError("Collision coordinates are outside the guarded range")
        result.append(tuple(point))
    return tuple(result)


def box(origin, axes):
    points = [add(origin, add(mul(axes[0], x), add(mul(axes[1], y), mul(axes[2], z))))
              for z in (0, 1) for y in (0, 1) for x in (0, 1)]
    faces = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    return points, [(a, b, c) for a, b, c, _ in faces] + [(a, c, d) for a, _, c, d in faces]


def control_box(points):
    p1, p2, p3, p4 = points
    a1 = mul(sub(add(p3, p4), add(p1, p2)), .5)
    axes = [a1, sub(p3, add(p1, a1)), sub(p4, add(p1, a1))]
    sizes = [length(axis) for axis in axes]
    basis = [unit(axis) for axis in axes]
    # Bounds.cs ray/sphere box tests reconstruct the shortest axis this way.
    index = 0 if sizes[0] < sizes[1] and sizes[0] < sizes[2] else 1 if sizes[1] < sizes[2] else 2
    basis[index] = cross(basis[(index+1) % 3], basis[(index+2) % 3])
    return box(p1, [mul(axis, size) for axis, size in zip(basis, sizes)])


def curved(start, end, radius, capsule=False, sphere=False, sides=12):
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("Collision radius must be finite and non-negative")
    delta = sub(end, start)
    distance = length(delta)
    axis = unit(delta) if distance > 1e-10 else (0., 0., 1.)
    u = unit(cross((0., 0., 1.) if abs(axis[2]) < .9 else (0., 1., 0.), axis))
    v = cross(axis, u)
    rings = []
    if capsule or sphere:
        for center, angles in ((start, range(-4, 1)), (start if sphere else end, range(0, 5))):
            for step in angles:
                angle = step * math.pi / 8
                rings.append((add(center, mul(axis, radius * math.sin(angle))), radius * math.cos(angle)))
    else:
        rings = [(start, radius), (end, radius)]
    points = [add(center, mul(add(mul(u, math.cos(2*math.pi*j/sides)), mul(v, math.sin(2*math.pi*j/sides))), ring_radius))
              for center, ring_radius in rings for j in range(sides)]
    triangles = []
    for row in range(len(rings)-1):
        for j in range(sides):
            a, b = row*sides+j, row*sides+(j+1) % sides
            triangles.extend(((a, b, a+sides), (b, b+sides, a+sides)))
    if not (capsule or sphere):
        points.extend((start, end))
        for j in range(sides):
            triangles.extend(((len(points)-2, (j+1) % sides, j), (len(points)-1, sides+j, sides+(j+1) % sides)))
    # Coincident pole/equator rows must not manufacture visible degenerate faces.
    triangles = [face for face in triangles if length(cross(sub(points[face[1]], points[face[0]]), sub(points[face[2]], points[face[0]]))) > 1e-12]
    return points, triangles


def packet(scene):
    """Bounded mesh samples for the React viewer, retaining full-scene counts."""
    if scene is None:
        return None
    geometries = [geometry for geometry in scene.geometries if geometry.triangles]
    selected = geometries if len(geometries) <= 96 else [geometries[round(i*(len(geometries)-1)/95)] for i in range(96)]
    total = sum(len(geometry.triangles) for geometry in selected)
    groups = []
    budget = 1500
    for index, geometry in enumerate(selected):
        count = len(geometry.triangles)
        allowance = min(count, max(1, int((1500-len(selected))*count/max(1, total))+1), budget)
        budget -= allowance
        indices = range(count) if allowance >= count else sorted({round(i*(count-1)/max(1, allowance-1)) for i in range(allowance)})
        groups.append({"id": str(index), "name": geometry.component, "material_index": geometry.material_index,
                       "source_triangle_count": count,
                       "triangles": [[list(geometry.vertices[vertex]) for vertex in geometry.triangles[i]] for i in indices]})
    shown = sum(len(group["triangles"]) for group in groups)
    return {"schema_version": 1, "read_only": True, "groups": groups, "bounds": scene.bounds,
            "triangle_count": scene.render_triangle_count, "displayed_triangles": shown,
            "group_count": len(geometries), "truncated": shown < scene.render_triangle_count,
            "primitive_counts": dict(scene.primitive_counts), "material_count": scene.material_count,
            "scope": "Triangle winding and derived face normals; curved primitives use display tessellation. Composite transforms applied. Physics contact margins/behavior require game testing."}
