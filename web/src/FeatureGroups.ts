/** 분석 주제의 입력 Feature를 사용자가 읽을 수 있는 묶음으로만 분류한다.
 * 이 분류는 ML 입력·출력 계약이나 Feature 중요도 계산이 아니다. 계약의 정본은 /api/topics다.
 */
type FeatureGroup = { readonly id: string; readonly title: string; readonly description: string;
  readonly features: readonly string[] };

const activityFeatureGroups: readonly FeatureGroup[] = [
  {
    id: "file", title: "파일 활동", description: "파일 생성·수정·접근·이름 변경",
    features: [
      "cnt_file_create", "cnt_file_del", "cnt_file_write", "cnt_file_rename",
      "cnt_large_write", "cnt_rename_ext_change", "max_write_size",
      "delta_file_vol", "avg_write_chunk_size", "cv_write_chunk_size",
      "uniq_file_touched", "uniq_new_ext_on_rename", "uniq_file_ext",
      "uniq_dir_touched", "avg_filename_entropy", "delta_avg_filename_entropy",
      "avg_file_path_depth", "ratio_hidden_file_ops", "read_write_bytes_ratio",
      "ratio_rename_burst", "ratio_large_write", "ratio_ext_change_on_rename",
      "avg_ops_per_file",
    ],
  },
  {
    id: "process", title: "프로세스 활동", description: "실행·종료·명령 및 활동 패턴",
    features: [
      "cnt_proc_exec", "cnt_proc_completed_in_batch", "uniq_proc_image",
      "velocity_proc_exec", "max_cmd_len", "ratio_no_ui", "ratio_proc_churn",
    ],
  },
  {
    id: "network", title: "네트워크 활동", description: "통신량·연결·목적지·송신 비율",
    features: [
      "net_bytes_per_sec", "cnt_net_tcp", "cnt_net_udp", "velocity_net_conn",
      "uniq_dest_port", "uniq_dest_ip", "ratio_private_ip", "max_url_len",
      "avg_net_payload_out", "ratio_top1_dest_bytes_out",
      "ratio_outbound_traffic", "uniq_browser_pages",
    ],
  },
  {
    id: "other", title: "활동량·시간 특성", description: "입출력 및 관측 구간의 활동 특성",
    features: ["io_bytes_per_sec", "ratio_offhour_activity"],
  },
];

/** 수정 CICFlowMeter가 흐름마다 출력하는 통계. 이름은 도구의 출력 헤더 그대로다. */
const networkFlowFeatureGroups: readonly FeatureGroup[] = [
  {
    id: "flow-volume", title: "흐름 규모", description: "지속 시간·패킷 수·바이트·방향 비율",
    features: [
      "Flow Duration", "Total TCP Flow Time", "Total Fwd Packet", "Total Bwd packets",
      "Total Length of Fwd Packet", "Total Length of Bwd Packet", "Down/Up Ratio", "Fwd Act Data Pkts",
      "Subflow Fwd Packets", "Subflow Fwd Bytes", "Subflow Bwd Packets", "Subflow Bwd Bytes",
    ],
  },
  {
    id: "flow-rate", title: "전송 속도", description: "초당 바이트·패킷",
    features: ["Flow Bytes/s", "Flow Packets/s", "Fwd Packets/s", "Bwd Packets/s"],
  },
  {
    id: "packet-length", title: "패킷 길이", description: "방향별·전체 패킷 길이와 세그먼트 크기",
    features: [
      "Fwd Packet Length Max", "Fwd Packet Length Min", "Fwd Packet Length Mean", "Fwd Packet Length Std",
      "Bwd Packet Length Max", "Bwd Packet Length Min", "Bwd Packet Length Mean", "Bwd Packet Length Std",
      "Packet Length Min", "Packet Length Max", "Packet Length Mean", "Packet Length Std",
      "Packet Length Variance", "Average Packet Size", "Fwd Segment Size Avg", "Bwd Segment Size Avg",
      "Fwd Seg Size Min",
    ],
  },
  {
    id: "inter-arrival", title: "도착 간격", description: "패킷 사이 시간(IAT)",
    features: [
      "Flow IAT Mean", "Flow IAT Std", "Flow IAT Max", "Flow IAT Min",
      "Fwd IAT Total", "Fwd IAT Mean", "Fwd IAT Std", "Fwd IAT Max", "Fwd IAT Min",
      "Bwd IAT Total", "Bwd IAT Mean", "Bwd IAT Std", "Bwd IAT Max", "Bwd IAT Min",
    ],
  },
  {
    id: "tcp-flags", title: "TCP 플래그", description: "연결 수립·종료·재설정 신호",
    features: [
      "Fwd PSH Flags", "Bwd PSH Flags", "Fwd RST Flags", "Bwd RST Flags", "FIN Flag Count",
      "SYN Flag Count", "RST Flag Count", "PSH Flag Count", "ACK Flag Count", "CWR Flag Count", "ECE Flag Count",
    ],
  },
  {
    id: "header-window", title: "헤더·윈도", description: "헤더 길이와 초기 TCP 윈도 크기",
    features: ["Fwd Header Length", "Bwd Header Length", "FWD Init Win Bytes", "Bwd Init Win Bytes"],
  },
  {
    id: "bulk", title: "대량 전송", description: "Bulk 구간의 평균 바이트·패킷·속도",
    features: [
      "Fwd Bytes/Bulk Avg", "Fwd Packet/Bulk Avg", "Fwd Bulk Rate Avg",
      "Bwd Bytes/Bulk Avg", "Bwd Packet/Bulk Avg", "Bwd Bulk Rate Avg",
    ],
  },
  {
    id: "active-idle", title: "활성·유휴", description: "흐름이 활성·유휴 상태였던 시간",
    features: ["Active Mean", "Active Std", "Active Max", "Active Min", "Idle Mean", "Idle Std", "Idle Max", "Idle Min"],
  },
];

/** 주제별 표시 묶음. 묶음이 없는 주제의 Feature는 '그 밖의 입력 Feature'로 보인다. */
export const topicFeatureGroups: Record<string, readonly FeatureGroup[]> = {
  activity_anomaly: activityFeatureGroups,
  network_flow_anomaly: networkFlowFeatureGroups,
};
// 주제마다 Feature 이름이 겹치지 않으므로 결과 화면은 주제를 몰라도 이름으로 묶음을 찾는다.
const allFeatureGroups = Object.values(topicFeatureGroups).flat();

/** 결과에 실제 보존된 Feature만 표시한다. 알려지지 않은 이름도 누락하지 않는다. */
export function groupFeatures(features: Record<string, number | null>) {
  const seen = new Set<string>();
  const groups: Array<{ id: string; title: string; description: string;
    entries: Array<readonly [string, number | null]> }> = allFeatureGroups.map(group => {
    const ordered = group.features.filter(name => Object.prototype.hasOwnProperty.call(features, name));
    const entries = ordered.map(name => { seen.add(name); return [name, features[name]] as const; });
    return { id: group.id, title: group.title, description: group.description, entries };
  }).filter(group => group.entries.length > 0);
  const unknown = Object.entries(features).filter(([name]) => !seen.has(name));
  if (unknown.length) groups.push({
    id: "unclassified", title: "그 밖의 입력 Feature", description: "현재 분류에 없는 모델 입력",
    entries: unknown,
  });
  return groups;
}
