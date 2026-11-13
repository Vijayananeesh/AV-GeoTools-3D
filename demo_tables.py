"""Demo data with 20 wells at irregular (random) places and a plume that follows the groundwater flow. Pure numpy."""
import numpy as np

X0, Y0 = 760000.0, 1015000.0
W, H = 400.0, 300.0
DL = 0.5                                  # reporting limit (detection limit) of the demo analysis
SRC = np.array([X0 + 130.0, Y0 + 165.0])  # where the contamination starts
FLOW = np.deg2rad(-25.0)                  # groundwater flows toward east-south-east
U = np.array([np.cos(FLOW), np.sin(FLOW)])
N = np.array([-U[1], U[0]])
UNITS = ["Fill", "Clay", "Sand", "Silty sand", "Gravel"]


def ground(x, y):
    return 52.0 + 0.035 * (x - X0) - 0.02 * (y - Y0) + 1.8 * np.sin((x - X0) / 55.0) * np.cos((y - Y0) / 70.0)


def plume_conc(x, y, depth):
    """Noise-free concentration (ug/L) at a place and depth below ground."""
    d = np.array([x, y]) - SRC
    s, t = float(d @ U), float(d @ N)
    along = np.exp(-s ** 2 / (2 * 25.0 ** 2)) if s < 0 else np.exp(-s ** 2 / (2 * 130.0 ** 2))
    sp = max(s, 0.0)
    across = np.exp(-t ** 2 / (2 * (16.0 + 0.13 * sp) ** 2))
    d0, sd = 5.0 + 0.016 * sp, 2.3 + 0.004 * sp
    return float(180.0 * along * across * np.exp(-(depth - d0) ** 2 / (2 * sd ** 2)))


def make_demo20(seed=7):
    """Returns dict(collars=[(id, x, y, ground_z, depth)], lith=[(id, from, to, unit)],
    plume=[(id, depth, elevation, conc, detected)])."""
    rng = np.random.default_rng(seed)
    wells = []

    def free(p):
        return all(np.hypot(*(p - q)) >= 10.0 for q in wells)

    tries = 0
    while len(wells) < 5 and tries < 8000:                  # a tight cluster at the source
        tries += 1
        p = SRC + rng.normal(0, 22, 2)
        if X0 + 8 <= p[0] <= X0 + W - 8 and Y0 + 8 <= p[1] <= Y0 + H - 8 and free(p):
            wells.append(p)
    while len(wells) < 12 and tries < 16000:                # then wells scattered along the plume path
        tries += 1
        p = SRC + rng.uniform(-30, 230) * U + rng.normal(0, 38) * N
        if X0 + 8 <= p[0] <= X0 + W - 8 and Y0 + 8 <= p[1] <= Y0 + H - 8 and free(p):
            wells.append(p)
    while len(wells) < 20:                                   # the rest are anywhere on the site
        p = np.array([rng.uniform(X0 + 8, X0 + W - 8), rng.uniform(Y0 + 8, Y0 + H - 8)])
        if free(p):
            wells.append(p)
    rng.shuffle(wells)
    collars, lith, plume = [], [], []
    for i, p in enumerate(wells, 1):
        hid = "MW%02d" % i
        x, y = float(p[0]), float(p[1])
        z = round(float(ground(x, y)) + float(rng.normal(0, 0.15)), 2)
        depth = float(np.round(rng.uniform(12, 24), 1))
        collars.append((hid, round(x, 1), round(y, 1), z, depth))
        fx, fy = (x - X0) / W, (y - Y0) / H
        t_fill = float(np.clip(1.0 + 1.2 * np.sin(6 * fx + 2 * fy) + rng.normal(0, 0.25), 0.4, 2.6))
        t_clay = float(np.clip(2.0 + 5.0 * (1 - fx) + 1.5 * fy + rng.normal(0, 0.5), 0.8, 9.0))
        t_sand = float(np.clip(3.5 + 4.0 * fx + rng.normal(0, 0.6), 1.5, 9.0))
        t_silt = float(np.clip(5.5 * np.sin(np.pi * fy) * (0.2 + fx) - 1.8 + rng.normal(0, 0.4), 0, 6))
        t_silt = 0.0 if t_silt < 0.4 else t_silt            # the silty sand pinches out in places
        a = 0.0
        for nm, t in zip(UNITS, [t_fill, t_clay, t_sand, t_silt, 99.0]):
            if t <= 0:
                continue
            b = min(a + t, depth)
            lith.append((hid, round(a, 2), round(b, 2), nm))
            a = b
            if a >= depth:
                break
        n = int(rng.integers(2, 7))
        for dep in np.unique(np.round(rng.uniform(1.0, depth - 0.5, n) * 2) / 2):
            c = plume_conc(x, y, float(dep)) * float(np.exp(rng.normal(0, 0.22)))
            plume.append((hid, float(dep), round(z - float(dep), 2), c, bool(c >= DL)))
    # groundwater levels: highest in the west and north, falling toward the east-south-east (separate random stream,
    # so adding them does not change the wells, lithology or plume above)
    g = np.random.default_rng(seed + 101)
    gw = []
    for hid, x, y, z, depth in collars:
        d_ = np.array([x, y]) - SRC
        s, tt = float(d_ @ U), float(d_ @ N)
        head = 50.0 - 0.018 * s + 0.0045 * tt + 0.25 * np.sin((x - X0) / 60.0) + float(g.normal(0, 0.08))
        head = min(head, z - 1.0)
        gw.append((hid, round(z - head, 2), round(head, 2)))
    # groundwater analyses: each result comes from a screened interval (top and bottom depth below ground),
    # with a plume that sits in the water below the water table (separate random stream again)
    g2 = np.random.default_rng(seed + 202)
    gwres = []
    for (hid, x, y, z, depth), (_h, dtw, head) in zip(collars, gw):
        top = round(round((dtw + float(g2.uniform(0.8, 2.0))) * 2) / 2, 1)
        bot = round(min(top + float(g2.choice([2.0, 3.0, 4.0])), depth - 0.3), 1)
        if bot - top < 1.5:
            continue
        d_ = np.array([x, y]) - SRC
        s, tt = float(d_ @ U), float(d_ @ N)
        along = np.exp(-s ** 2 / (2 * 30.0 ** 2)) if s < 0 else np.exp(-s ** 2 / (2 * 150.0 ** 2))
        sp = max(s, 0.0)
        across = np.exp(-tt ** 2 / (2 * (20.0 + 0.15 * sp) ** 2))
        zc = head - 3.5 - 0.012 * sp
        zm = z - 0.5 * (top + bot)
        sd = 2.5 + 0.004 * sp
        c = 900.0 * along * across * np.exp(-(zm - zc) ** 2 / (2 * sd ** 2)) * float(np.exp(g2.normal(0, 0.2)))
        gwres.append((hid, top, bot, float(c), bool(c >= DL)))
    return {"collars": collars, "lith": lith, "plume": plume, "gw": gw, "gwres": gwres}
