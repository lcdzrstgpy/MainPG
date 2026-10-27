"""Category-level image prompt presets.

These are the production, single-line prompts from the v16 downstream library.
They are injected into the existing batch-level guideline wrapper only when a
title is confidently classified; the wrapper still owns the four-grid,
divider, product-fidelity, typography and main-panel rules.

Do not add markdown, newlines or ``{}`` placeholders here: the prompt is
formatted after injection and must stay a plain single line.
"""

from __future__ import annotations

CATEGORY_IMAGE_PROMPTS: dict[str, str] = {
    "home_kitchen": (
        "HOME & KITCHEN: Scene: warm domestic oak/linen/ceramic; kitchen counter, closet shelf, "
        "dining table, entryway console. Focus: cookware=working edge/thickness; storage=real "
        "contents/stack; bedding=weave/fold; decor=silhouette/finish. Light: soft 4800K window "
        "light; vary direction and temperature per panel. Show: capacity with real contents, "
        "wall/stack thickness, grain/glaze/weave, dinner-plate or A4 scale. Avoid: appliance "
        "staging, invented lids/racks, food-safety claims, plastic sheen, decor hiding the "
        "silhouette, hands/fingers, fine rulers."
    ),
    "apparel_accessories": (
        "ADULT APPAREL & ACCESSORIES: Scene: body-in-space; bedroom mirror, entryway bench, cafe "
        "table, stone ledge. Route: garment=neckline drape, no visible body; footwear=one worn/one "
        "profile; bag=handle/lining; eyewear/jewelry=metal hinge macro; hat/scarf=weave/fall. Light: "
        "natural window or low sun; vary direction and temperature per panel. Show: true silhouette, "
        "stitch/weave macro, working hardware, size vs A4 sheet. Avoid: full faces/bodies, mannequin "
        "gaps, painted clothing, warped straps, invented logos, floating shoes, jewelry-skin merge, "
        "hands/fingers."
    ),
    "pet_supplies": (
        "PET SUPPLIES: Scene: low pet-height floor life; wool rug, entryway tile, feeding corner, "
        "sofa edge. Focus: toys=chew resistance; beds=thickness/washability; wearables=fit range; "
        "grooming=bristle/blade geometry. Light: soft window light, low camera; vary direction and "
        "temperature per panel. Show: stitching/buckle/clip strength, bite/scratch resistance, "
        "dinner-plate scale, washable covers. Avoid: full pets/faces, distress, treats implied as "
        "included, invented straps, hard gripping, cartoon styling, chew-safety claims, hands/fingers."
    ),
    "office_stationery": (
        "OFFICE & STATIONERY: Scene: strict grid-order desk; light oak, white laminate, dark felt, "
        "warm gray paper. Focus: writing=paper edge/binding; organizers=compartment count/capacity; "
        "desktop tools=mechanism; calculators=key layout. Light: cool 5200K daylight; vary direction "
        "and surface tone per panel. Show: real capacity, page/binding/paper edge, mechanism action, "
        "scale vs A4 sheet. Avoid: invented labels, floating pens/paper, impossible stacks, "
        "rainbow clutter, fake leather, readable screens, hands/fingers, fine rulers."
    ),
    "sports_outdoor": (
        "SPORTS & OUTDOOR: Scene: honest training/trail surfaces; pale oak, park grass/stone, "
        "campsite log, gym rubber. Focus: yoga/fitness=grip/compression; camping=packed size; "
        "fishing=hook/knot; bottles=lid seal. Light: directional natural light; vary direction, "
        "time and temperature per panel. Show: grip macro, compression under load, packed size, "
        "load-bearing stitching, scale vs mat. Avoid: stunts, floating gear, impossible tension, "
        "branded machines, full faces, sweat, performance claims, hands/fingers."
    ),
    "auto_accessories": (
        "AUTO ACCESSORIES: Scene: tight interior; dashboard, seat gap, trunk carpet, garage concrete. "
        "Focus: sunshade=coverage/edge fit; organizers=gap fit; mats=edge/anti-slip; towels=pile; "
        "covers=stitch/fit. Light: hard sun through glass plus soft fill; vary direction and time "
        "per panel. Show: fit gap/installed position, coverage/attachment, thickness/absorbency, "
        "folded size, texture macro. Avoid: car brands, electronics, airbag/safety claims, "
        "windshield obstruction, impossible universal fit, glued suction cups, hands/fingers."
    ),
    "hand_tools": (
        "HAND TOOLS & HARDWARE: Scene: industrial workshop; oiled workbench, pegboard, plaster wall "
        "point, concrete floor. Focus: drivers/bits=tip fit; wrenches=jaw/ratchet; measuring=markings; "
        "fasteners=thread/coating; hooks=anchor path. Light: hard raking work light; vary direction "
        "and surface tone per panel. Show: working-end geometry, joint/ratchet movement, grip "
        "texture, set count, scale vs A4 sheet. Avoid: power tools, impossible mechanisms, floating "
        "screws, unsafe use, plastic chrome, invented stamps, load claims, hands/fingers, fine rulers."
    ),
    "passive_tech": (
        "PASSIVE TECH ACCESSORIES: Scene: clean white/charcoal; matte desk, tablet stand, backpack "
        "felt, charcoal flat-lay. Focus: case=cutout/lip; sleeve=lining/fit; cable organizer=capacity; "
        "keyboard cover=key alignment; cleaner=fiber. Light: soft 5200K with controlled reflections; "
        "vary direction and surface tone per panel. Show: cutout alignment, button coverage/lip, "
        "lining/padding, profile thickness, zero-gap fit. Avoid: device logos, magnets/MagSafe, "
        "invented ports, impossible fit, floating devices, fake leather, fingerprints, drop claims, "
        "hands/fingers."
    ),
    "beauty_tools": (
        "BEAUTY & PERSONAL-CARE TOOLS: Scene: bright clinical-clean vanity; white marble, pale stone, "
        "acrylic, sink ceramic. Focus: brushes=bristle density; nail tools=grit/edge; razors=guard/"
        "handle; hair tools=surface; cleansing=fiber. Light: even 5000K vanity light; vary direction "
        "and temperature per panel. Show: bristle density/shape, blade edge, grip/balance, kit "
        "layout, scale vs compact case. Avoid: medical/acne claims, flawed skin close-ups, non-product "
        "makeup, brand text, unhygienic staging, splayed bristles, sterile claims, hands/fingers."
    ),
    "adult_games": (
        "ADULT GAMES & PUZZLES: Scene: warm tungsten table; walnut, dark bar cart, leather "
        "side table, deep green felt. Focus: cards=face/back print; board games=components/insert; "
        "puzzles=artwork/cut; fidget=material/mechanism. Light: 2700K practical light; vary direction "
        "and temperature per panel. Show: all components, exact artwork, piece "
        "thickness, box size vs A4 sheet, storage insert. Avoid: children/family, child-safety claims, "
        "gambling, IP characters, distorted faces/numbers, missing pieces, cartoon styling, "
        "hands/fingers."
    ),
    "garden_tools": (
        "GARDEN & PATIO TOOLS: Scene: dappled daylight; weathered potting bench, painted railing, "
        "dark soil bed, mossy stone patio. Focus: pots=drainage/wall; pruners=blade/bypass; "
        "trowels=scoop/edge; seed trays=cell count; watering=spout. Light: natural daylight with "
        "leaf shadow; vary direction and time per panel. Show: blade/cut geometry, pot drainage/"
        "wall thickness, grip geometry, tray cell count, scale vs plant pot. Avoid: large "
        "landscaping, power equipment, invasive/fake plants, invented blooms, chemical claims, "
        "branded tags, hands/fingers, fine rulers."
    ),
    "general": (
        "GENERAL: Scene: neutral plaster/oak; warm gray plaster, white plaster corner, warm gray "
        "paper flat-lay, light stone ledge. Light: soft diffused key; vary direction and surface "
        "tone per panel. Show: complete product, strongest real detail macro, scale vs A4 sheet or "
        "dinner plate, all included parts. Avoid: category-specific props, invented accessories, "
        "brand text, clutter, unsupported claims, hands/fingers, fine rulers."
    ),
}
