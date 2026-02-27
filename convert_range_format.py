"""
Range 格式转换工具
将 JSON 字典格式的 range 转换为逗号分隔的字符串格式
"""

import json
import sys
from pathlib import Path
from typing import Dict, Union


def dict_to_range_string(range_dict: Dict[str, float]) -> str:
    """
    将字典格式的 range 转换为字符串格式
    
    Args:
        range_dict: {"2d2c": 0.069, "2h2c": 0.069, ...}
        
    Returns:
        "2d2c:0.069,2h2c:0.069,..."
    """
    parts = []
    for hand, prob in range_dict.items():
        parts.append(f"{hand}:{prob:.3f}")
    return ",".join(parts)


def range_string_to_dict(range_str: str) -> Dict[str, float]:
    """
    将字符串格式的 range 转换为字典格式
    
    Args:
        range_str: "2d2c:0.069,2h2c:0.069,..."
        
    Returns:
        {"2d2c": 0.069, "2h2c": 0.069, ...}
    """
    result = {}
    if not range_str:
        return result
    
    parts = range_str.split(',')
    for part in parts:
        part = part.strip()
        if ':' in part:
            hand, prob_str = part.rsplit(':', 1)
            try:
                prob = float(prob_str)
                result[hand.strip()] = prob
            except ValueError:
                print(f"警告: 无法解析概率值 '{prob_str}' for hand '{hand}'")
    
    return result


def convert_from_json_file(json_file: str, output_file: str = None):
    """
    从 JSON 文件读取 range 并转换为字符串格式
    
    Args:
        json_file: 输入的 JSON 文件路径
        output_file: 输出文件路径（可选，默认打印到控制台）
    """
    print(f"读取文件: {json_file}")
    
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 如果是字典格式，直接转换
    if isinstance(data, dict):
        range_str = dict_to_range_string(data)
        
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(range_str)
            print(f"✅ 已保存到: {output_file}")
        else:
            print("\n转换结果:")
            print(range_str)
    else:
        print("❌ JSON 文件格式不正确，应该是字典格式 {hand: prob}")


def convert_from_json_string(json_str: str) -> str:
    """
    从 JSON 字符串转换
    
    Args:
        json_str: JSON 字符串，如 '{"2d2c": 0.069, "2h2c": 0.069}'
        
    Returns:
        range 字符串
    """
    data = json.loads(json_str)
    return dict_to_range_string(data)


def main(range_dict: Dict[str, float]):
    """主函数 - 演示用法"""
    
    # 示例 1: 直接转换字典
    print("=" * 60)
    print("示例 1: 字典转字符串")
    print("=" * 60)
    
    range_str = dict_to_range_string(range_dict)
    print(f"输入: {range_dict}")
    print(f"输出: {range_str}")
    
    # 示例 2: 反向转换
    print("\n" + "=" * 60)
    print("示例 2: 字符串转字典")
    print("=" * 60)
    
    range_str = "2d2c:0.069,2h2c:0.069,2s2c:0.574"
    range_dict = range_string_to_dict(range_str)
    print(f"输入: {range_str}")
    print(f"输出: {range_dict}")
    
    # 示例 3: 从 JSON 字符串转换
    print("\n" + "=" * 60)
    print("示例 3: JSON 字符串转换")
    print("=" * 60)
    
    json_str = '{"2d2c": 0.069, "2h2c": 0.069, "2s2c": 0.574}'
    result = convert_from_json_string(json_str)
    print(f"输入: {json_str}")
    print(f"输出: {result}")
    
    print("\n" + "=" * 60)
    print("使用说明")
    print("=" * 60)
    print("命令行用法:")
    print("  python convert_range_format.py <json_file> [output_file]")
    print("\nPython 代码用法:")
    print("  from convert_range_format import dict_to_range_string, range_string_to_dict")
    print("  range_str = dict_to_range_string({'AhAs': 1.0, 'KdKc': 0.5})")


if __name__ == "__main__":
    range_dict_ip = {"2d2c": 0.069, "2h2c": 0.069, "2s2c": 0.574, "2s2d": 0.688, "2s2h": 0.688, "3d3c": 0.191, "3h3c": 0.191, "3s3c": 0.751, "3s3d": 0.66, "3s3h": 0.66, "4d4c": 0.424, "4h4c": 0.424, "4h4d": 0.117, "4s4c": 0.74, "4s4d": 0.881, "4s4h": 0.881, "5c4c": 0.293, "5d4d": 0.22, "5d5c": 0.425, "5h4h": 0.22, "5h5c": 0.425, "5h5d": 0.297, "5s4s": 0.067, "5s5c": 0.7, "5s5d": 1, "5s5h": 1, "6h5h": 0.181, "6s5s": 0.548, "7d7c": 0.716, "7h6h": 0.002, "7h7c": 0.716, "7h7d": 0.196, "7s6s": 0.441, "7s7c": 0.851, "7s7d": 0.945, "7s7h": 0.945, "8c7c": 0.24, "8d7d": 0.266, "8d8c": 0.711, "8h7h": 0.266, "8h8c": 0.711, "8s7s": 0.394, "8s8c": 0.806, "8s8d": 0.943, "8s8h": 0.943, "9c8c": 0.366, "9d8d": 0.234, "9d9c": 0.586, "9h8h": 0.234, "9h9c": 0.586, "9h9d": 0.854, "9s8s": 0.268, "9s9c": 0.759, "9s9d": 1, "9s9h": 1, "Ad2d": 0.502, "Ad3d": 0.531, "Ad4d": 0.594, "Ad5d": 0.659, "Ad7d": 0.646, "Ad8d": 0.532, "Ad9d": 0.507, "AdJc": 0.257, "AdJd": 0.329, "AdJh": 0.331, "AdJs": 0.577, "AdKc": 0.084, "AdKd": 0.089, "AdKh": 0.094, "AdKs": 0.351, "AdQc": 0.155, "AdQd": 0.188, "AdQh": 0.191, "AdQs": 0.437, "AdTd": 0.22, "AdTh": 0.228, "AdTs": 0.358, "Ah2h": 0.502, "Ah3h": 0.531, "Ah4h": 0.594, "Ah5h": 0.659, "Ah6h": 0.294, "Ah7h": 0.646, "Ah8h": 0.532, "Ah9h": 0.507, "AhAd": 0.136, "AhJc": 0.257, "AhJd": 0.331, "AhJh": 0.329, "AhJs": 0.577, "AhKc": 0.084, "AhKd": 0.094, "AhKh": 0.089, "AhKs": 0.351, "AhQc": 0.155, "AhQd": 0.191, "AhQh": 0.188, "AhQs": 0.437, "AhTd": 0.228, "AhTh": 0.22, "AhTs": 0.358, "As2s": 0.923, "As3s": 0.914, "As4s": 0.943, "As5s": 0.909, "As6s": 0.431, "As7s": 0.937, "As8s": 0.902, "As9s": 0.762, "AsAd": 0.204, "AsAh": 0.204, "AsJc": 0.382, "AsJd": 0.374, "AsJh": 0.374, "AsJs": 0.608, "AsKc": 0.16, "AsKd": 0.165, "AsKh": 0.165, "AsKs": 0.386, "AsQc": 0.263, "AsQd": 0.267, "AsQh": 0.267, "AsQs": 0.487, "AsTd": 0.234, "AsTh": 0.234, "AsTs": 0.334, "Jc9c": 0.344, "Jd9d": 0.48, "JdJc": 0.664, "JdTd": 0.527, "Jh9h": 0.48, "JhJc": 0.664, "JhJd": 0.911, "JhTh": 0.527, "Js9s": 0.698, "JsJc": 0.708, "JsJd": 0.997, "JsJh": 0.997, "JsTs": 0.822, "Kc9c": 0.059, "KcJc": 0.081, "KcJd": 0.149, "KcJh": 0.149, "KcJs": 0.35, "KcQc": 0.186, "KcQd": 0.074, "KcQh": 0.074, "KcQs": 0.309, "KcTd": 0.034, "KcTh": 0.034, "KcTs": 0.098, "Kd2d": 0.113, "Kd3d": 0.121, "Kd4d": 0.23, "Kd5d": 0.261, "Kd7d": 0.062, "Kd9d": 0.615, "KdJc": 0.239, "KdJd": 0.557, "KdJh": 0.55, "KdJs": 0.716, "KdKc": 0.358, "KdQc": 0.182, "KdQd": 0.55, "KdQh": 0.544, "KdQs": 0.761, "KdTd": 0.65, "KdTh": 0.18, "KdTs": 0.117, "Kh2h": 0.113, "Kh3h": 0.121, "Kh4h": 0.23, "Kh5h": 0.261, "Kh6h": 0.138, "Kh7h": 0.062, "Kh9h": 0.615, "KhJc": 0.239, "KhJd": 0.55, "KhJh": 0.557, "KhJs": 0.716, "KhKc": 0.358, "KhKd": 0.999, "KhQc": 0.182, "KhQd": 0.544, "KhQh": 0.55, "KhQs": 0.761, "KhTd": 0.18, "KhTh": 0.65, "KhTs": 0.117, "Ks2s": 0.178, "Ks3s": 0.197, "Ks4s": 0.261, "Ks5s": 0.261, "Ks6s": 0.261, "Ks7s": 0.169, "Ks8s": 0.103, "Ks9s": 0.812, "KsJc": 0.788, "KsJd": 0.683, "KsJh": 0.683, "KsJs": 0.836, "KsKc": 0.433, "KsKd": 1, "KsKh": 1, "KsQc": 0.533, "KsQd": 0.723, "KsQh": 0.723, "KsQs": 0.935, "KsTd": 0.261, "KsTh": 0.261, "KsTs": 0.902, "Qc9c": 0.056, "QcJc": 0.171, "QcJd": 0.223, "QcJh": 0.223, "QcJs": 0.56, "Qd9d": 0.346, "QdJc": 0.237, "QdJd": 0.351, "QdJh": 0.348, "QdJs": 0.467, "QdQc": 0.501, "QdTd": 0.525, "Qh9h": 0.346, "QhJc": 0.237, "QhJd": 0.348, "QhJh": 0.351, "QhJs": 0.467, "QhQc": 0.501, "QhQd": 0.978, "QhTh": 0.525, "Qs9s": 0.601, "QsJc": 0.751, "QsJd": 0.47, "QsJh": 0.47, "QsJs": 0.6, "QsQc": 0.531, "QsQd": 1, "QsQh": 1, "QsTs": 0.863, "Td9d": 0.493, "Th9h": 0.493, "ThTd": 0.009, "Ts9s": 0.647, "TsTd": 0.069, "TsTh": 0.069}
    range_dict_oop = {"2d2c": 0.127, "2h2c": 0.007, "2s2c": 0.267, "2s2d": 0.001, "3d3c": 0.228, "3h3c": 0.218, "3s3c": 0.351, "3s3d": 0.002, "3s3h": 0.002, "4d4c": 0.409, "4h4c": 0.409, "4h4d": 0.456, "4s4c": 0.443, "4s4d": 0.321, "4s4h": 0.388, "5c4c": 0.251, "5d4d": 0.833, "5d5c": 0.683, "5h4h": 1, "5h5c": 0.803, "5h5d": 0.736, "5s4s": 0.999, "5s5c": 0.684, "5s5d": 0.001, "5s5h": 0.489, "6h5h": 0.137, "6s6h": 0.133, "7d7c": 1, "7h6h": 0.126, "7h7c": 1, "7h7d": 0.908, "7s7c": 0.961, "7s7d": 0.742, "7s7h": 0.898, "8c7c": 0.582, "8d8c": 0.867, "8h8c": 0.899, "8h8d": 0.64, "8s8c": 0.76, "8s8d": 0.657, "8s8h": 0.748, "9c8c": 0.306, "9d9c": 0.567, "9h9c": 0.666, "9h9d": 0.845, "9s8s": 0.116, "9s9c": 0.716, "9s9d": 0.971, "9s9h": 0.996, "Ad2d": 0.673, "Ad3d": 0.701, "Ad4d": 0.572, "Ad5d": 0.427, "Ad7d": 0.636, "Ad8d": 0.657, "Ad9d": 0.373, "AdJc": 0.202, "AdJd": 0.32, "AdJh": 0.361, "AdJs": 0.465, "AdKh": 0.009, "AdKs": 0.026, "AdQc": 0.211, "AdQd": 0.218, "AdQh": 0.259, "AdQs": 0.381, "AdTd": 0.032, "AdTh": 0.094, "AdTs": 0.005, "Ah2h": 0.759, "Ah3h": 0.752, "Ah4h": 0.564, "Ah5h": 0.53, "Ah6h": 0.128, "Ah7h": 0.721, "Ah8h": 0.72, "Ah9h": 0.42, "AhJc": 0.16, "AhJd": 0.317, "AhJh": 0.375, "AhJs": 0.453, "AhKd": 0.005, "AhKs": 0.033, "AhQc": 0.167, "AhQd": 0.247, "AhQh": 0.296, "AhQs": 0.415, "AhTd": 0.02, "AhTh": 0.033, "AhTs": 0.009, "As2s": 0.695, "As3s": 0.708, "As4s": 0.536, "As5s": 0.472, "As6s": 0.348, "As7s": 0.668, "As8s": 0.647, "As9s": 0.486, "AsJc": 0.334, "AsJd": 0.388, "AsJh": 0.442, "AsJs": 0.529, "AsKd": 0.003, "AsKh": 0.02, "AsQc": 0.417, "AsQd": 0.293, "AsQh": 0.351, "AsQs": 0.52, "AsTd": 0.078, "AsTh": 0.153, "AsTs": 0.062, "Jc9c": 0.075, "Jd9d": 1, "JdJc": 0.483, "JdTd": 0.425, "Jh9h": 1, "JhJc": 0.505, "JhJd": 0.557, "JhTh": 0.535, "Js9s": 1, "JsJc": 0.532, "JsJd": 0.656, "JsJh": 0.656, "JsTs": 0.488, "Kc5c": 0.001, "Kc7c": 0.115, "Kc8c": 0.049, "Kc9c": 0.018, "KcJc": 0.004, "KcQc": 0.076, "KcTd": 0.03, "KcTh": 0.176, "KcTs": 0.214, "Kd2d": 1, "Kd3d": 1, "Kd4d": 1, "Kd5d": 1, "Kd7d": 1, "Kd8d": 1, "Kd9d": 1, "KdJd": 0.343, "KdJh": 0.524, "KdJs": 0.604, "KdQd": 0.369, "KdQh": 0.568, "KdQs": 0.719, "KdTd": 0.519, "KdTh": 0.684, "KdTs": 0.648, "Kh2h": 1, "Kh3h": 1, "Kh4h": 1, "Kh5h": 1, "Kh6h": 0.161, "Kh7h": 1, "Kh8h": 1, "Kh9h": 1, "KhJd": 0.502, "KhJh": 0.642, "KhJs": 0.695, "KhQd": 0.572, "KhQh": 0.804, "KhQs": 0.902, "KhTd": 0.734, "KhTh": 0.705, "KhTs": 0.754, "Ks2s": 1, "Ks3s": 1, "Ks4s": 1, "Ks5s": 1, "Ks6s": 0.004, "Ks7s": 1, "Ks8s": 1, "Ks9s": 1, "KsJd": 0.658, "KsJh": 0.824, "KsJs": 0.676, "KsQc": 0.002, "KsQd": 0.597, "KsQh": 0.766, "KsQs": 0.767, "KsTd": 0.643, "KsTh": 0.722, "KsTs": 0.55, "Qc9c": 0.173, "QcJc": 0.278, "Qd9d": 1, "QdJd": 0.003, "QdJh": 0.116, "QdJs": 0.15, "QdQc": 0.229, "QdTd": 0.327, "Qh9h": 1, "QhJd": 0.077, "QhJh": 0.176, "QhJs": 0.212, "QhQc": 0.239, "QhQd": 0.223, "QhTh": 0.419, "Qs9s": 1, "QsJd": 0.229, "QsJh": 0.318, "QsJs": 0.398, "QsQc": 0.249, "QsQd": 0.248, "QsQh": 0.249, "QsTs": 0.418, "Td9d": 0.579, "Th9h": 0.586, "ThTd": 0.297, "Ts9s": 0.706, "TsTd": 0.395, "TsTh": 0.381}
    main(range_dict=range_dict_oop)
