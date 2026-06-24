import httpx
import time

BASE_URL = "http://127.0.0.1:8080/trading"

def main():
    print("Starting AI Workflow End-to-End Test")
    
    # 1. Start the loop
    print("\n1.  Starting trading loop...")
    r = httpx.post(f"{BASE_URL}/loop/start", json={
        "interval_minutes": 1,
        "symbols": ["BTC-USD"],
        "strategy": "combined"
    })
    print(f"Loop start response: {r.status_code} - {r.text}")
    
    # 2. Trigger on-demand analysis
    print("\n2.  Triggering on-demand AI Analysis...")
    r = httpx.post(f"{BASE_URL}/analyze", json={"symbol": "ETH-USD"}, timeout=60.0)
    print(f"Analysis response status: {r.status_code}")
    try:
        analysis = r.json()
        print(f"Signal generated: {analysis.get('direction', 'UNKNOWN')}")
        print(f"Reasoning: {analysis.get('reasoning', '')[:100]}...")
    except Exception as e:
        print(f"Failed to parse analysis: {e}")
        
    print("\n Waiting 5 seconds for background processes...")
    time.sleep(5)
    
    # 3. Check signals
    print("\n3.  Checking recorded signals...")
    r = httpx.get(f"{BASE_URL}/signals")
    if r.status_code == 200:
        signals = r.json().get('signals', [])
        print(f"Total signals found: {len(signals)}")
        if signals:
            print(f"Latest signal: {signals[0].get('symbol')} {signals[0].get('direction')}")
    else:
        print(f"Failed to fetch signals: {r.status_code}")
        
    # 4. Check portfolio history
    print("\n4.  Checking portfolio snapshot...")
    r = httpx.get(f"{BASE_URL}/portfolio/history")
    if r.status_code == 200:
        snaps = r.json().get('snapshots', [])
        print(f"Portfolio snapshots found: {len(snaps)}")
        if snaps:
            print(f"Latest balance: ${snaps[0].get('total_value')}")
    
    # 5. Stop the loop
    print("\n5.  Stopping trading loop...")
    r = httpx.post(f"{BASE_URL}/loop/stop")
    print(f"Loop stop response: {r.status_code} - {r.text}")
    print("Test complete.")

if __name__ == "__main__":
    main()
