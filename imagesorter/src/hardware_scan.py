import psutil
import onnxruntime as ort

def scan_hardware():
    """Scans hardware and returns a dict of capabilities and suggested settings."""
    info = {
        "cpu_cores": psutil.cpu_count(logical=True),
        "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "onnx_providers": ort.get_available_providers(),
        "suggestions": {}
    }

    # Suggest AI execution provider
    if 'CUDAExecutionProvider' in info['onnx_providers'] or 'DmlExecutionProvider' in info['onnx_providers']:
        info['suggestions']['ai_provider'] = "GPU Accelerated"
    else:
        info['suggestions']['ai_provider'] = "CPU Execution"

    # Suggest Queue Worker Threads (Currently we use 1 for simplicity,
    # but this lays the groundwork for multi-threading file ops)
    if info['cpu_cores'] and info['cpu_cores'] >= 8:
        info['suggestions']['queue_threads'] = 4
    elif info['cpu_cores'] and info['cpu_cores'] >= 4:
        info['suggestions']['queue_threads'] = 2
    else:
        info['suggestions']['queue_threads'] = 1

    return info

def print_hardware_report():
    hw = scan_hardware()
    print("=== Hardware Scan ===")
    print(f"CPU Cores: {hw['cpu_cores']}")
    print(f"RAM: {hw['memory_total_gb']} GB")
    print(f"ONNX Providers: {', '.join(hw['onnx_providers'])}")
    print("\n--- Suggestions ---")
    print(f"AI Provider: {hw['suggestions']['ai_provider']}")
    print(f"Recommended Worker Threads: {hw['suggestions']['queue_threads']}")

if __name__ == "__main__":
    print_hardware_report()
