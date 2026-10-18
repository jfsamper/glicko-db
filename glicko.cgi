#!/home/petroglo/virtualenv/colombiago.org/glicko/3.11/bin/python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cgi_bridge import run

run()
