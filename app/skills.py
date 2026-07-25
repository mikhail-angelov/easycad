"""On-demand generation skills (SPEC15).

The base system prompt is kept lean. Specialised, heavier recipes — things the
model tends to get wrong or invent badly (threads, gears, …) — live here as
separate "skills" and are injected into the generator prompt ONLY when relevant.

Relevance is decided upstream by the triage LLM call (see `refiner.triage`),
which returns a list of skill tags from `SKILL_TAGS`. `render()` turns those
tags into an extra system message for `llm.generate_code`.

Each recipe is a VERIFIED snippet (runs clean in our cadquery 2.8.0 worker) —
treat that as a hard requirement when adding new skills, mirroring the
"verified before submission" rule of the cadquery-llm-skill project.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    name: str      # stable tag used by triage + render
    menu: str      # one line for the triage menu: when to load it
    recipe: str    # the guidance + verified code, injected into the generator


# The one and only verified thread example. It is embedded verbatim in the recipe
# the LLM sees AND executed by the regression test (tests/test_spec15.py), so the
# taught snippet and the tested snippet can never drift apart. Editing the recipe
# means editing THIS constant — the test runs exactly what the model is shown.
THREAD_EXAMPLE = '''\
import cadquery as cq

# ── Parameters (worked example: M12 x 80, coarse pitch, 30 mm thread) ──
MAJOR_D = 12.0        # thread major (nominal) diameter, mm
PITCH = 1.75          # coarse pitch for M12
BOLT_LEN = 80.0       # shank + thread, mm
THREAD_LEN = 30.0     # threaded length at the free end, mm
HEX_AF = 18.0         # hex head across-flats for M12, mm
HEAD_H = 8.4          # head height (~0.7 x D), mm

_H = PITCH * (3 ** 0.5) / 2          # ISO fundamental triangle height (no math import)
r_maj = MAJOR_D / 2
r_min = r_maj - 5 * _H / 8           # ISO minor radius
SHANK_Z = BOLT_LEN - THREAD_LEN      # Z where the thread starts
SHANK_R = r_maj - 0.2                # shank JUST under the major radius (see note below)
CREST_HW = PITCH * 0.10              # half the crest flat width
ROOT_HW = PITCH * 0.47               # half the root flat width


# Thread = shell of ruled surfaces between four helical wires (two at the crest /
# major radius, two at the root / minor radius), NOT a profile swept along a
# helix (sweeping wobbles with isFrenet=False and fails to export with True).
# This is how the cq_warehouse library builds threads.
def make_thread(z0):
    def hx(radius, zoff):
        return cq.Wire.makeHelix(pitch=PITCH, height=THREAD_LEN, radius=radius).translate((0, 0, z0 + zoff))
    a0 = hx(r_maj, -CREST_HW); a1 = hx(r_maj, CREST_HW)   # crest (outer)
    r1 = hx(r_min, ROOT_HW); r0 = hx(r_min, -ROOT_HW)     # root  (inner)
    faces = [cq.Face.makeRuledSurface(a0, a1), cq.Face.makeRuledSurface(a1, r1),
             cq.Face.makeRuledSurface(r1, r0), cq.Face.makeRuledSurface(r0, a0)]
    for t in (0.0, 1.0):                                  # flat end caps close the shell
        pts = [w.positionAt(t) for w in (a0, a1, r1, r0)]
        faces.append(cq.Face.makeFromWires(cq.Wire.makePolygon(pts + [pts[0]])))
    return cq.Solid.makeSolid(cq.Shell.makeShell(faces))


head = cq.Workplane("XY").polygon(6, HEX_AF / (3 ** 0.5 / 2)).extrude(-HEAD_H)
body = cq.Workplane("XY").circle(SHANK_R).extrude(SHANK_Z)   # unthreaded shank
core = cq.Workplane("XY").workplane(offset=SHANK_Z).circle(r_min + 0.01).extrude(THREAD_LEN)
# Assemble in THIS order and with these radii — both matter:
#   * SHANK_R is 0.2 mm UNDER r_maj: a shank at exactly r_maj is tangent to the
#     thread crests and the boolean deletes the whole thread.
#   * Start from the solid head+body, then union the threaded rod (core u thread)
#     LAST. The thread solid is shell-built and fragile in booleans; if it is the
#     FIRST operand the later unions drop the head and shank instead.
bolt = head.union(body).union(core.union(make_thread(SHANK_Z)))
# The head is extruded downward from z=0, so lift the whole bolt to sit ON the
# XY plane: z_min = 0, Z up, centred on XY — the coordinate contract for a part.
result = bolt.translate((0, 0, HEAD_H))
'''


_THREAD_RECIPE = (
    "## Skill: real metric thread (ISO)\n\n"
    "When the request is a threaded fastener/feature (bolt, screw, stud, nut,\n"
    "tapped hole, threaded rod), produce a REAL helical thread — NOT a plain\n"
    "cylinder standing in for the thread.\n\n"
    "Critical rules (each is a wrong-result trap; all are applied in the worked\n"
    "example below — reproduce them exactly, only adapting the Parameters):\n"
    "- Build the thread as a SHELL OF RULED SURFACES between four helical wires\n"
    "  (two at the major radius = crest, two at the minor = root), NOT by sweeping\n"
    "  a profile along a helix. Sweeping wobbles (isFrenet=False) or fails to\n"
    "  export (isFrenet=True). This is the cq_warehouse technique.\n"
    "- Use only `cq`; get sqrt(3) as `3 ** 0.5`, never import the math module.\n"
    "- The shank sits 0.2 mm UNDER the major radius. A shank at exactly r_maj is\n"
    "  tangent to the thread crests and the boolean deletes the thread.\n"
    "- Assemble head+body first and union the threaded rod LAST — the shell-built\n"
    "  thread solid is fragile in booleans and drops the head+shank if it is\n"
    "  first: head.union(body).union(core.union(thread)) keeps everything.\n"
    "- INTERNAL thread (nut / tapped hole): cut make_thread(...) and an r_min bore\n"
    "  from the body instead of unioning it.\n\n"
    "Standard metric coarse pitches: M6->1.0, M8->1.25, M10->1.5, M12->1.75,\n"
    "M16->2.0, M20->2.5. Hex head across-flats: M6->10, M8->13, M10->17, M12->18,\n"
    "M16->24. Head height ~= 0.7 x D.\n\n"
    "Verified worked example (runs clean in cadquery 2.8.0), inside ```python:\n\n"
    "```python\n"
    + THREAD_EXAMPLE
    + "```\n"
)


SKILLS: dict[str, Skill] = {
    "thread": Skill(
        name="thread",
        menu='"thread" — the request involves a real screw thread: bolt, screw, '
             "stud, threaded rod, nut, or tapped/threaded hole.",
        recipe=_THREAD_RECIPE,
    ),
}

# Valid tags the triage call may return.
SKILL_TAGS: list[str] = list(SKILLS)

# Menu block listed in the triage prompt so the classifier knows what exists.
SKILL_MENU: str = "\n".join(f"    - {SKILLS[t].menu}" for t in SKILL_TAGS)


def clean_tags(tags: object) -> list[str]:
    """Keep only known tags, deduped, order-preserving. Defensive against a
    malformed LLM response: a scalar (e.g. `"skills": 1`) or any non-sequence
    yields [] rather than raising; a bare string is treated as a single tag."""
    if isinstance(tags, str):
        tags = [tags]
    elif not isinstance(tags, (list, tuple)):
        return []
    seen: list[str] = []
    for t in tags:
        t = str(t).strip().lower()
        if t in SKILLS and t not in seen:
            seen.append(t)
    return seen


def render(tags: list[str] | None) -> str | None:
    """Assemble the recipes for `tags` into one system-message body, or None."""
    picked = clean_tags(tags)
    if not picked:
        return None
    body = "\n\n".join(SKILLS[t].recipe for t in picked)
    return (
        "The following skill(s) apply to this request. Follow them exactly, "
        "including the verified code patterns:\n\n" + body
    )
