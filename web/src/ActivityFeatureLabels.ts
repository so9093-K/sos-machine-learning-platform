/** 사용자 화면 표시명만 한글로 제공하며 모델의 원본 Feature 키는 유지한다. */
const labels: Record<string, string> = {
  io_bytes_per_sec:"초당 입출력 바이트",net_bytes_per_sec:"초당 네트워크 바이트",
  cnt_file_create:"파일 생성 횟수",cnt_file_del:"파일 삭제 횟수",
  cnt_file_write:"파일 쓰기 횟수",cnt_file_rename:"파일 이름 변경 횟수",
  cnt_proc_exec:"프로세스 실행 횟수",ratio_offhour_activity:"업무시간 외 활동 비율",
  uniq_dest_ip:"고유 목적지 IP 수",uniq_dest_port:"고유 목적지 포트 수",
  uniq_file_touched:"고유 접근 파일 수",max_write_size:"최대 파일 쓰기 크기",
  avg_filename_entropy:"평균 파일명 엔트로피",
  ratio_hidden_file_ops:"숨김 파일 작업 비율",
};
export function activityFeatureLabel(name:string):string { return labels[name] ?? name; }
