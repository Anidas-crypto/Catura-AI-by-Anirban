#!/usr/bin/env python3
"""
✅ Catura AI - Auto Version Updater for Render
Usage: python update-version.py
This updates version in all files before deployment
"""

import re
import sys

def update_version():
    print("\n" + "="*60)
    print("🚀 CATURA AI - VERSION UPDATER")
    print("="*60 + "\n")
    
    try:
        # Read current version
        with open('version.txt', 'r') as f:
            version = f.read().strip()
        
        print(f"📍 Current version: {version}")
        
        # Parse version (major.minor.patch)
        parts = version.split('.')
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
        
        # Increment patch version
        patch += 1
        new_version = f"{major}.{minor}.{patch}"
        
        # 1. Update version.txt
        with open('version.txt', 'w') as f:
            f.write(new_version)
        print(f"✅ version.txt → {new_version}")
        
        # 2. Update index.html
        with open('index.html', 'r', encoding='utf-8') as f:
            html_content = f.read()
        html_content = re.sub(r'\?v=[\d.]+', f'?v={new_version}', html_content)
        with open('index.html', 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"✅ index.html → v{new_version}")
        
        # 3. Update auth.html
        with open('auth.html', 'r', encoding='utf-8') as f:
            auth_content = f.read()
        # Check if auth.html has version params (if not, skip)
        if '?v=' in auth_content:
            auth_content = re.sub(r'\?v=[\d.]+', f'?v={new_version}', auth_content)
            with open('auth.html', 'w', encoding='utf-8') as f:
                f.write(auth_content)
            print(f"✅ auth.html → v{new_version}")
        else:
            print(f"⏭️  auth.html → (no version params)")
        
        # 4. Update service-worker.js
        with open('service-worker.js', 'r', encoding='utf-8') as f:
            sw_content = f.read()
        sw_content = re.sub(
            r"CACHE_VERSION = '[^']+'", 
            f"CACHE_VERSION = '{new_version}'", 
            sw_content
        )
        with open('service-worker.js', 'w', encoding='utf-8') as f:
            f.write(sw_content)
        print(f"✅ service-worker.js → {new_version}")
        
        # 5. Update main.py
        with open('main.py', 'r', encoding='utf-8') as f:
            py_content = f.read()
        py_content = re.sub(
            r'"version": "[^"]+"',
            f'"version": "{new_version}"',
            py_content
        )
        with open('main.py', 'w', encoding='utf-8') as f:
            f.write(py_content)
        print(f"✅ main.py → {new_version}")
        
        print("\n" + "="*60)
        print(f"✨ VERSION UPDATED TO {new_version}!")
        print("="*60)
        print("\n📤 NEXT STEPS:\n")
        print("   1. git add .")
        print(f"   2. git commit -m '🚀 Bump version to {new_version}'")
        print("   3. git push origin main")
        print("\n✅ Render will auto-deploy! Users get fresh version.\n")
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}\n")
        sys.exit(1)

if __name__ == '__main__':
    update_version()