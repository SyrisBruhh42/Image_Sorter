import psutil
import onnxruntime as ort
from typing import List, Dict, Any, Optional

def get_recommended_providers() -> List[str]:
    """
    Returns an ordered priority list of available ONNX Execution Providers.
    Priority: CUDA -> DirectML -> CPU.
    Dynamically filters based on what `onnxruntime` detects.
    """
    available_providers: List[str] = ort.get_available_providers()
    priority_list: List[str] = ['CUDAExecutionProvider', 'DmlExecutionProvider', 'CPUExecutionProvider']

    # Filter and maintain priority order
    recommended: List[str] = [p for p in priority_list if p in available_providers]

    # Fallback in case none of the preferred are available (highly unlikely as CPU is standard)
    if not recommended:
        recommended = available_providers

    return recommended

def get_optimal_thread_count() -> int:
    """
    Calculates the optimal thread worker allocations based on physical vs. logical cores.
    Gracefully handles virtualized sandboxes/VMs where logical=False returns None.

    Formula used:
    - If physical cores detectable: max(1, min(physical_cores or (logical_cores // 2 or 1), 8))
    - If physical cores undetectable: max(1, (logical_cores or 2) - 1)
    """
    physical_cores: Optional[int] = psutil.cpu_count(logical=False)
    logical_cores: Optional[int] = psutil.cpu_count(logical=True)

    if physical_cores is not None:
        # Standard physical machine behavior
        base_count: int = physical_cores or (logical_cores // 2 if logical_cores else 1) or 1
        return max(1, min(base_count, 8))
    else:
        # VM/Sandbox behavior where physical_cores returns None
        base_count_fallback: int = (logical_cores or 2) - 1
        return max(1, base_count_fallback)

def scan_hardware() -> Dict[str, Any]:
    """
    Scans hardware and returns a dict of capabilities and suggested settings.
    Integrates the robust get_recommended_providers and get_optimal_thread_count functions.
    """
    mem: Any = psutil.virtual_memory()
    info: Dict[str, Any] = {
        "cpu_cores": psutil.cpu_count(logical=True),
        "memory_total_gb": round(mem.total / (1024**3), 2),
        "onnx_providers": ort.get_available_providers(),
        "suggestions": {}
    }

    # Suggest AI execution provider
    recommended_providers: List[str] = get_recommended_providers()
    if 'CUDAExecutionProvider' in recommended_providers or 'DmlExecutionProvider' in recommended_providers:
        info['suggestions']['ai_provider'] = "GPU Accelerated"
    else:
        info['suggestions']['ai_provider'] = "CPU Execution"

    # Suggest Queue Worker Threads based on the robust formula
    info['suggestions']['queue_threads'] = get_optimal_thread_count()

    return info

def print_hardware_report() -> None:
    """Prints a detailed hardware and configuration report."""
    hw: Dict[str, Any] = scan_hardware()
    print("=== Hardware Scan ===")
    print(f"CPU Cores (Logical): {hw['cpu_cores']}")
    print(f"RAM: {hw['memory_total_gb']} GB")
    print(f"ONNX Providers: {', '.join(hw['onnx_providers'])}")
    print("\n--- Suggestions ---")
    print(f"AI Provider: {hw['suggestions']['ai_provider']}")
    print(f"Recommended Worker Threads: {hw['suggestions']['queue_threads']}")

if __name__ == "__main__":
    print_hardware_report()
