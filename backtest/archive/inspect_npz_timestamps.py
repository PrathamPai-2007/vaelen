import os
import numpy as np

def inspect_npz_timestamps():
    processed_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'processed'))
    files = [f for f in os.listdir(processed_dir) if f.endswith('.npz')]
    print(f"Found {len(files)} NPZ files in {processed_dir}:")
    for f in files:
        path = os.path.join(processed_dir, f)
        data = np.load(path)['data']
        print(f"\nFile: {f} | Rows: {len(data)}")
        print(f"  Fields: {data.dtype.names}")
        if 'ts' in data.dtype.names:
            ts = data['ts']
            duration_ms = ts[-1] - ts[0]
            duration_sec = duration_ms / 1000.0 if ts[-1] > 1e11 else duration_ms
            duration_min = duration_sec / 60.0
            avg_tick_rate = len(data) / duration_sec if duration_sec > 0 else 0
            print(f"  Start TS: {ts[0]} | End TS: {ts[-1]}")
            print(f"  Duration: {duration_min:.2f} minutes ({duration_sec:.1f} sec)")
            print(f"  Avg Tick Rate: {avg_tick_rate:.2f} ticks/sec")

if __name__ == "__main__":
    inspect_npz_timestamps()
