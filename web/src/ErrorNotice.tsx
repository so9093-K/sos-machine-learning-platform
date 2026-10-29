import type { ReactNode } from "react";
import type { ErrorNotice as Notice } from "./api";

/** 실패한 조회·요청의 원인 문장과 다음 행동을 먼저 보이고, 오류 코드·요청 ID는 참조로 분리한다. */
export function ErrorNotice({ title, notice, children }: {
  title: string; notice: Notice; children?: ReactNode;
}) {
  return <div className="error-notice" role="alert">
    <strong>{title}</strong>
    <p>{notice.message}</p>
    {notice.guidance && <p>{notice.guidance}</p>}
    {notice.reference && <small className="alert-reference">{notice.reference}</small>}
    {children}
  </div>;
}
