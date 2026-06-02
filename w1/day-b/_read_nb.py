import json
with open('w1/day-b/assignment.ipynb', 'r', encoding='utf-8') as f:
    nb = json.load(f)
cells = nb['cells']

for i in [5, 6, 7, 8, 10, 11, 13, 14, 16, 17, 18, 19, 23]:
    cell = cells[i]
    print(f'===== CELL {i} [{cell["cell_type"]}] =====')
    print(''.join(cell['source']))
    print()
