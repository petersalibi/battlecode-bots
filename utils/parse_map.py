import json
import sys
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict, namedtuple

plt.style.use('seaborn-v0_8-dark')

filename = sys.argv[1]
match_data = None
with open(filename) as match_json:
    match_data = json.load(match_json)

WIDTH = match_data["match"]["width"]
HEIGHT = match_data["match"]["height"]

Point = namedtuple("Point", ("x", "y"))

ward_points = defaultdict(int)

events = match_data["events"]

for action_dict in events:
    if action_dict["kind"] == "wardPlacement":
        for ward_placed in action_dict["entries"]:
            new_point = Point(ward_placed["x"], ward_placed["y"])
            ward_points[new_point] += 1

n = len(ward_points)
x_arr = np.zeros((n,))
y_arr = np.zeros((n,))
freq_arr = np.zeros((n,))

for i, (k, freq) in enumerate(ward_points.items()):
    x, y = k
    x_arr[i] = x
    y_arr[i] = y
    freq_arr[i] = freq


fig, ax = plt.subplots()

print(freq_arr)
print(np.max(x_arr))

ax.hist2d(x_arr, y_arr, weights=freq_arr, bins=[int(np.max(x_arr) - np.min(x_arr)), int(np.max(y_arr) - np.min(y_arr))])
ax.set_xlim(0, WIDTH)
ax.set_ylim(0, HEIGHT)

plt.show()
