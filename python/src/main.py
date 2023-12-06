import sys

with open(sys.argv[1], "r", encoding="utf-8") as file:
    for line in file:
        print(line.strip())
