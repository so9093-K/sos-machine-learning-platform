"""사용자가 이해하고 활용하는 분석 주제의 입력 의미와 출력 계약.

주제는 특정 알고리즘이나 모델 출처(SOS 학습·생산 Pipeline 연결)에 묶이지 않는다. 입력 Feature와
출력 종류가 맞는 모델은 알고리즘과 관계없이 그 주제에서 적용·호출할 수 있다.
"""

from .ml_outputs import ANOMALY_SCORE_V1

# 사용자·호스트 단위로 집계한 숫자형 활동 Feature. 이름으로 대응하며 순서는 계약이 아니다.
ACTIVITY_FEATURES = ("io_bytes_per_sec", "net_bytes_per_sec", "cnt_file_create", "cnt_file_del", "cnt_file_write", "cnt_file_rename", "cnt_proc_exec", "cnt_net_tcp", "cnt_net_udp", "cnt_large_write", "cnt_rename_ext_change", "cnt_proc_completed_in_batch", "max_write_size", "ratio_offhour_activity", "delta_file_vol", "velocity_net_conn", "velocity_proc_exec", "delta_avg_filename_entropy", "avg_write_chunk_size", "cv_write_chunk_size", "uniq_browser_pages", "uniq_file_touched", "uniq_new_ext_on_rename", "uniq_proc_image", "uniq_dest_port", "uniq_dest_ip", "ratio_private_ip", "uniq_file_ext", "uniq_dir_touched", "avg_filename_entropy", "max_cmd_len", "max_url_len", "avg_file_path_depth", "avg_net_payload_out", "ratio_no_ui", "ratio_hidden_file_ops", "read_write_bytes_ratio", "ratio_rename_burst", "ratio_large_write", "ratio_ext_change_on_rename", "ratio_proc_churn", "ratio_top1_dest_bytes_out", "ratio_outbound_traffic", "avg_ops_per_file")

# 수정 CICFlowMeter(CNS2022 정정판 CIC-IDS2017을 만든 판본)가 흐름마다 출력하는 통계 Feature.
# 사용자가 같은 도구로 자신의 pcap에서 만든 CSV를 이름 변경 없이 적용할 수 있도록 출력 헤더를 그대로 쓴다.
# 망에 묶인 식별자(Flow ID·IP·포트·Protocol·Timestamp), 범주 표시값(ICMP Code·Type의 -1),
# 정상 학습 구간에서 값이 변하지 않는 URG 플래그 수는 입력에서 뺀다.
NETWORK_FLOW_FEATURES = ("Flow Duration", "Total Fwd Packet", "Total Bwd packets", "Total Length of Fwd Packet", "Total Length of Bwd Packet", "Fwd Packet Length Max", "Fwd Packet Length Min", "Fwd Packet Length Mean", "Fwd Packet Length Std", "Bwd Packet Length Max", "Bwd Packet Length Min", "Bwd Packet Length Mean", "Bwd Packet Length Std", "Flow Bytes/s", "Flow Packets/s", "Flow IAT Mean", "Flow IAT Std", "Flow IAT Max", "Flow IAT Min", "Fwd IAT Total", "Fwd IAT Mean", "Fwd IAT Std", "Fwd IAT Max", "Fwd IAT Min", "Bwd IAT Total", "Bwd IAT Mean", "Bwd IAT Std", "Bwd IAT Max", "Bwd IAT Min", "Fwd PSH Flags", "Bwd PSH Flags", "Fwd RST Flags", "Bwd RST Flags", "Fwd Header Length", "Bwd Header Length", "Fwd Packets/s", "Bwd Packets/s", "Packet Length Min", "Packet Length Max", "Packet Length Mean", "Packet Length Std", "Packet Length Variance", "FIN Flag Count", "SYN Flag Count", "RST Flag Count", "PSH Flag Count", "ACK Flag Count", "CWR Flag Count", "ECE Flag Count", "Down/Up Ratio", "Average Packet Size", "Fwd Segment Size Avg", "Bwd Segment Size Avg", "Fwd Bytes/Bulk Avg", "Fwd Packet/Bulk Avg", "Fwd Bulk Rate Avg", "Bwd Bytes/Bulk Avg", "Bwd Packet/Bulk Avg", "Bwd Bulk Rate Avg", "Subflow Fwd Packets", "Subflow Fwd Bytes", "Subflow Bwd Packets", "Subflow Bwd Bytes", "FWD Init Win Bytes", "Bwd Init Win Bytes", "Fwd Act Data Pkts", "Fwd Seg Size Min", "Active Mean", "Active Std", "Active Max", "Active Min", "Idle Mean", "Idle Std", "Idle Max", "Idle Min", "Total TCP Flow Time")

_TOPICS = (
    {
        "id": "activity_anomaly",
        "name": "활동 이상 탐지",
        "learning_type": "unsupervised",
        "task_type": "anomaly_detection",
        "required_features": ACTIVITY_FEATURES,
        "output_type": ANOMALY_SCORE_V1,
    },
    {
        "id": "network_flow_anomaly",
        "name": "네트워크 흐름 이상 탐지",
        "learning_type": "unsupervised",
        "task_type": "anomaly_detection",
        "required_features": NETWORK_FLOW_FEATURES,
        "output_type": ANOMALY_SCORE_V1,
    },
)


def topic(topic_id: str) -> dict | None:
    """식별자로 찾은 분석 주제. 없으면 None."""
    return next((item for item in topics() if item["id"] == topic_id), None)


def topics() -> list[dict]:
    """제공하는 분석 주제. 학습 유형·분석 과제는 주제의 분류 속성이다."""
    return [{**item, "required_features": list(item["required_features"])} for item in _TOPICS]
