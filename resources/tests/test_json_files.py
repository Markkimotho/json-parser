#!/usr/bin/env python3
"""
Test runner for JSON test files
Tests all JSON files in subdirectories to validate parser functionality
"""

import sys
import json
from pathlib import Path

# Add parent directory to path to import parser
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app import Lexer, Parser, JSONParseError

def test_json_file(filepath, should_be_valid=None):
    """Test a single JSON file"""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        lexer = Lexer(content)
        parser = Parser(lexer)
        result = parser.parse()
        
        return True, result
    except JSONParseError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)

def categorize_file(filepath):
    """Determine if a file should be valid or invalid based on its path"""
    path_str = str(filepath)
    
    if 'invalid' in path_str.lower():
        return False  # Should be invalid
    elif 'valid' in path_str.lower() or 'error' not in path_str.lower():
        return True  # Should be valid
    else:
        return True  # Default to valid

def main():
    test_dir = Path(__file__).parent
    
    results = {
        'total': 0,
        'passed': 0,
        'failed': 0,
        'valid_files': [],
        'invalid_files': [],
        'errors': []
    }
    
    print("=" * 80)
    print("JSON FILE TEST RUNNER")
    print("=" * 80)
    print()
    
    # Find all JSON files
    json_files = sorted(test_dir.glob('**/*.json'))
    
    if not json_files:
        print("No JSON files found!")
        return
    
    print(f"Found {len(json_files)} JSON files\n")
    
    for filepath in json_files:
        results['total'] += 1
        should_be_valid = categorize_file(filepath)
        parsed_ok, result = test_json_file(filepath)
        
        relative_path = filepath.relative_to(test_dir)
        
        if parsed_ok:
            if should_be_valid:
                results['passed'] += 1
                results['valid_files'].append(str(relative_path))
                print(f"[OK] {relative_path}")
            else:
                results['failed'] += 1
                results['errors'].append(f"{relative_path}: Should have failed but parsed successfully")
                print(f"[ERROR] {relative_path} (SHOULD HAVE FAILED)")
        else:
            if not should_be_valid:
                results['passed'] += 1
                results['invalid_files'].append(str(relative_path))
                print(f"[OK] {relative_path} (correctly rejected)")
            else:
                results['failed'] += 1
                results['errors'].append(f"{relative_path}: {result}")
                print(f"[ERROR] {relative_path}")
    
    print()
    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"Total Files:    {results['total']}")
    print(f"Passed:         {results['passed']}")
    print(f"Failed:         {results['failed']}")
    print(f"Success Rate:   {(results['passed'] / results['total'] * 100):.1f}%")
    print()
    
    if results['errors']:
        print("ERRORS:")
        for error in results['errors']:
            print(f"  • {error}")
    
    return 0 if results['failed'] == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
