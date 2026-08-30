import psutil
import onnxruntime as ort
from typing import Dict, Any, List
from .logger import logger

# Priority order for AI Execution Providers across platforms (Linux, Windows, macOS)
PROVIDER_PRIORITY: List[str] = [
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "ROCMExecutionProvider",
    "MIGraphXExecutionProvider",
    "OpenVINOExecutionProvider",
    "DmlExecutionProvider",
    "CoreMLExecutionProvider",
    "CPUExecutionProvider"
]


def get_prioritized_providers() -> List[str]:
    """
    Scans available ONNX Runtime execution providers and ranks them
    according to hardware performance priority.

    Returns:
        List[str]: Available providers sorted from highest to lowest capability.
    """
    try:
        available = set(ort.get_available_providers())
    except Exception as e:
        logger.warning(f"Failed to query ONNX available providers: {e}")
        available = {"CPUExecutionProvider"}

    prioritized = [p for p in PROVIDER_PRIORITY if p in available]
    # Include any unexpected available providers at the end before CPU
    for p in available:
        if p not in prioritized:
            prioritized.insert(-1 if "CPUExecutionProvider" in prioritized else len(prioritized), p)

    if "CPUExecutionProvider" not in prioritized:
        prioritized.append("CPUExecutionProvider")

    return prioritized


def scan_hardware() -> Dict[str, Any]:
    """
    Scans hardware and returns detailed system metrics along with optimization suggestions.

    Returns:
        Dict[str, Any]: Hardware capabilities and recommended application settings.
    """
    logical_cores = psutil.cpu_count(logical=True) or 1
    physical_cores = psutil.cpu_count(logical=False) or logical_cores
    ram_gb = round(psutil.virtual_memory().total / (1024**3), 2)

    providers = get_prioritized_providers()
    top_provider = providers[0] if providers else "CPUExecutionProvider"

    if top_provider in ("TensorrtExecutionProvider", "CUDAExecutionProvider", "ROCMExecutionProvider", "MIGraphXExecutionProvider"):
        ai_suggestion = f"GPU Accelerated ({top_provider})"
    elif top_provider in ("OpenVINOExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider"):
        ai_suggestion = f"Hardware Accelerated ({top_provider})"
    else:
        ai_suggestion = "CPU Execution (SIMD)"

    # Thread calculation based on physical vs logical cores
    recommended_threads = max(1, min(physical_cores, 8))

    info: Dict[str, Any] = {
        "physical_cores": physical_cores,
        "logical_cores": logical_cores,
        "memory_total_gb": ram_gb,
        "onnx_providers": providers,
        "top_provider": top_provider,
        "suggestions": {
            "ai_provider": ai_suggestion,
            "queue_threads": recommended_threads
        }
    }

    logger.info(f"Hardware scan completed: {physical_cores} physical / {logical_cores} logical cores, {ram_gb} GB RAM, Top Provider: {top_provider}")
    return info


def print_hardware_report() -> None:
    """Prints a human-readable hardware optimization report to stdout."""
    hw = scan_hardware()
    print("=== Image Sorter Hardware Scan ===")
    print(f"Physical CPU Cores: {hw['physical_cores']}")
    print(f"Logical CPU Cores:  {hw['logical_cores']}")
    print(f"System RAM:          {hw['memory_total_gb']} GB")
    print(f"Available Providers: {', '.join(hw['onnx_providers'])}")
    print("\n--- Recommended Settings ---")
    print(f"AI Provider:                {hw['suggestions']['ai_provider']}")
    print(f"Recommended Worker Threads: {hw['suggestions']['queue_threads']}")


if __name__ == "__main__":
    print_hardware_report()
