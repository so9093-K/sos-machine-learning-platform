/** 활동 집계 Feature를 사용자가 읽을 수 있는 주제별로만 분류한다.
 * 이 분류는 ML 입력·출력 계약이나 Feature 중요도 계산이 아니다.
 */
export const activityFeatureGroups = [
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
] as const;

/** 결과에 실제 보존된 Feature만 표시한다. 알려지지 않은 이름도 누락하지 않는다. */
export function groupActivityFeatures(features: Record<string, number | null>) {
  const seen = new Set<string>();
  const groups: Array<{ id: string; title: string; description: string;
    entries: Array<readonly [string, number | null]> }> = activityFeatureGroups.map(group => {
    const entries = group.features.filter(name => Object.prototype.hasOwnProperty.call(features, name))
      .map(name => { seen.add(name); return [name, features[name]] as const; });
    return { id: group.id, title: group.title, description: group.description, entries };
  }).filter(group => group.entries.length > 0);
  const unknown = Object.entries(features).filter(([name]) => !seen.has(name));
  if (unknown.length) groups.push({
    id: "unclassified", title: "그 밖의 입력 Feature", description: "현재 분류에 없는 모델 입력",
    entries: unknown,
  });
  return groups;
}
