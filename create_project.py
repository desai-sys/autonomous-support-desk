#!/usr/bin/env python3
"""
Script to create the complete CloudOps project structure from scratch.
Run this script to create all necessary files and folders.
"""

import os
import json

def create_project_structure():
    """Create the complete project structure."""
    
    # Define the project structure
    structure = {
        'src': {
            'core': ['__init__.py'],
            'tools': ['__init__.py'],
            'agents': ['__init__.py'],
            'services': ['__init__.py'],
            'models': ['__init__.py']
        },
        'deployment': [],
        'scripts': [],
        'config': []
    }
    
    # Create directories and __init__.py files
    for folder, subfolders in structure.items():
        os.makedirs(folder, exist_ok=True)
        
        if isinstance(subfolders, dict):
            for subfolder, files in subfolders.items():
                subfolder_path = os.path.join(folder, subfolder)
                os.makedirs(subfolder_path, exist_ok=True)
                
                for file in files:
                    file_path = os.path.join(subfolder_path, file)
                    if not os.path.exists(file_path):
                        with open(file_path, 'w') as f:
                            f.write('# Auto-generated __init__.py\n')
        else:
            for file in subfolders:
                file_path = os.path.join(folder, file)
                if not os.path.exists(file_path):
                    with open(file_path, 'w') as f:
                        f.write('# Auto-generated file\n')
    
    print("✅ Project structure created successfully!")
    print("\nNext steps:")
    print("1. Run the individual file creation scripts")
    print("2. Update configuration values")
    print("3. Deploy to AWS")

if __name__ == "__main__":
    create_project_structure()