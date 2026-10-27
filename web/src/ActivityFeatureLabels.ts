/** 사용자 화면 표시명만 한글로 제공하며 모델의 원본 Feature 키는 유지한다.
 * 표시명은 Feature 이름의 직역이며, 집계 정의의 정본은 원본 Feature 생성 로직이다.
 */
const labels: Record<string, string> = {
  io_bytes_per_sec:"초당 입출력 바이트",net_bytes_per_sec:"초당 네트워크 바이트",
  cnt_file_create:"파일 생성 횟수",cnt_file_del:"파일 삭제 횟수",
  cnt_file_write:"파일 쓰기 횟수",cnt_file_rename:"파일 이름 변경 횟수",
  cnt_proc_exec:"프로세스 실행 횟수",cnt_net_tcp:"TCP 네트워크 이벤트 수",
  cnt_net_udp:"UDP 네트워크 이벤트 수",cnt_large_write:"대용량 쓰기 횟수",
  cnt_rename_ext_change:"확장자가 바뀐 이름 변경 횟수",
  cnt_proc_completed_in_batch:"구간 내 종료된 프로세스 수",
  max_write_size:"최대 파일 쓰기 크기",ratio_offhour_activity:"업무시간 외 활동 비율",
  delta_file_vol:"파일 용량 변화량",velocity_net_conn:"네트워크 연결 변화 속도",
  velocity_proc_exec:"프로세스 실행 변화 속도",
  delta_avg_filename_entropy:"평균 파일명 엔트로피 변화량",
  avg_write_chunk_size:"평균 쓰기 단위 크기",cv_write_chunk_size:"쓰기 단위 크기 변동계수",
  uniq_browser_pages:"고유 브라우저 페이지 수",uniq_file_touched:"고유 접근 파일 수",
  uniq_new_ext_on_rename:"이름 변경 시 새 확장자 종류 수",
  uniq_proc_image:"고유 프로세스 이미지 수",
  uniq_dest_port:"고유 목적지 포트 수",uniq_dest_ip:"고유 목적지 IP 수",
  ratio_private_ip:"사설 IP 통신 비율",uniq_file_ext:"고유 파일 확장자 수",
  uniq_dir_touched:"고유 접근 디렉터리 수",avg_filename_entropy:"평균 파일명 엔트로피",
  max_cmd_len:"최대 명령줄 길이",max_url_len:"최대 URL 길이",
  avg_file_path_depth:"평균 파일 경로 깊이",avg_net_payload_out:"평균 송신 페이로드 크기",
  ratio_no_ui:"UI 없는 활동 비율",ratio_hidden_file_ops:"숨김 파일 작업 비율",
  read_write_bytes_ratio:"읽기/쓰기 바이트 비율",ratio_rename_burst:"이름 변경 집중 비율",
  ratio_large_write:"대용량 쓰기 비율",ratio_ext_change_on_rename:"이름 변경 중 확장자 변경 비율",
  ratio_proc_churn:"프로세스 교체 비율",ratio_top1_dest_bytes_out:"최다 목적지 송신 바이트 비율",
  ratio_outbound_traffic:"송신 트래픽 비율",avg_ops_per_file:"파일당 평균 작업 수",
};
export function activityFeatureLabel(name:string):string { return labels[name] ?? name; }
