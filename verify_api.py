import urllib.request
import json

URL = "http://localhost:5000/api/author/17000/profile"

try:
    with urllib.request.urlopen(URL) as response:
        data = json.loads(response.read().decode())
        
    print("\n--- Profile ---")
    print(json.dumps(data.get('profile'), indent=2))
    
    print("\n--- Yearly Stats (Sample) ---")
    stats = data.get('yearly_stats', [])
    if stats:
        print(json.dumps(stats[:3], indent=2))
        
    # Check if conf_count and journal_count are present
    if stats and 'conf_count' in stats[0] and 'journal_count' in stats[0]:
        print("\nVerification: SUCCESS (New stats fields found)")
    else:
        print("\nVerification: FAILED (New stats fields missing)")
        
except Exception as e:
    print(f"Error: {e}")
