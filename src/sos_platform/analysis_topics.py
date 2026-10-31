"""사용자가 이해하고 활용하는 분석 주제의 입력 의미와 출력 계약.

주제는 특정 알고리즘이나 등록 Pipeline에 묶이지 않는다. 입력 Feature와 출력 종류가 맞는 모델은
알고리즘과 관계없이 그 주제에서 적용·호출할 수 있다. 등록 Pipeline은 주제에 모델을 공급하는
선택적 Workflow이며, 자기 정의의 topic으로 주제를 가리킨다.
"""

from .ml_outputs import ANOMALY_SCORE_V1

# 사용자·호스트 단위로 집계한 숫자형 활동 Feature. 이름으로 대응하며 순서는 계약이 아니다.
ACTIVITY_FEATURES = ("io_bytes_per_sec", "net_bytes_per_sec", "cnt_file_create", "cnt_file_del", "cnt_file_write", "cnt_file_rename", "cnt_proc_exec", "cnt_net_tcp", "cnt_net_udp", "cnt_large_write", "cnt_rename_ext_change", "cnt_proc_completed_in_batch", "max_write_size", "ratio_offhour_activity", "delta_file_vol", "velocity_net_conn", "velocity_proc_exec", "delta_avg_filename_entropy", "avg_write_chunk_size", "cv_write_chunk_size", "uniq_browser_pages", "uniq_file_touched", "uniq_new_ext_on_rename", "uniq_proc_image", "uniq_dest_port", "uniq_dest_ip", "ratio_private_ip", "uniq_file_ext", "uniq_dir_touched", "avg_filename_entropy", "max_cmd_len", "max_url_len", "avg_file_path_depth", "avg_net_payload_out", "ratio_no_ui", "ratio_hidden_file_ops", "read_write_bytes_ratio", "ratio_rename_burst", "ratio_large_write", "ratio_ext_change_on_rename", "ratio_proc_churn", "ratio_top1_dest_bytes_out", "ratio_outbound_traffic", "avg_ops_per_file")

_TOPICS = (
    {
        "id": "activity_anomaly",
        "name": "활동 이상 탐지",
        "learning_type": "unsupervised",
        "task_type": "anomaly_detection",
        "required_features": ACTIVITY_FEATURES,
        "output_type": ANOMALY_SCORE_V1,
    },
)


def topics() -> list[dict]:
    """제공하는 분석 주제. 학습 유형·분석 과제는 주제의 분류 속성이다."""
    return [{**item, "required_features": list(item["required_features"])} for item in _TOPICS]
