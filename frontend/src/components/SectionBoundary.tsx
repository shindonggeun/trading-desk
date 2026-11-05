import { Component, type ReactNode } from "react";

/**
 * 한 구역의 오류를 그 구역 안에 가둔다 — 배포 직후 열어 둔 화면에서 옛 청크(lazy import)가 404 이거나
 * 차트 라이브러리가 throw 해도 분석 화면 전체가 하얗게 되지 않게.
 */
export default class SectionBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
