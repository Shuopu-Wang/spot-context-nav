#!/usr/bin/env python3
import os
import sys
import unittest

test_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'test')
sys.path.insert(0, test_dir)
loader = unittest.defaultTestLoader
suite = loader.discover(test_dir, pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
