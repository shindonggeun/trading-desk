// #776 배포 인계 계약. Jenkins 단계 대신 아래 어댑터를 주입해 단독으로 시험한다.
// 운영 Pipeline에서 사용할 어댑터와 잡 권한은 별도로 검증해야 한다.

def runHandoff(String revision, Map adapter) {
    if (!(revision ==~ /[0-9a-f]{40}/)) {
        throw new IllegalArgumentException('전체 develop 커밋 SHA가 필요하다')
    }
    return adapter.withLock('trading-desk-prod-deploy') {
        if (!adapter.eligible(revision)) {
            return [result: 'SKIPPED']
        }

        Map release = adapter.prepare(revision)
        validateRelease(release)
        if (release.revision != revision) {
            throw new IllegalArgumentException('준비된 이미지의 커밋이 요청과 다르다')
        }

        for (String role in ['api', 'collector', 'analyst']) {
            String name = "/trading-desk/trading-desk-backend-${role}"
            // start는 알려진 핸들을 반환하거나 하위 작업이 생기지 않은 상태에서만 실패한다.
            def child = adapter.start(name, release.subMap(['revision', 'imageId', 'agent', 'daemon']))
            int status
            try {
                // 반환 값은 #808 배포 스크립트의 종료 코드다. 잡이 종료된 뒤에만 반환한다.
                status = adapter.await(child)
            } catch (Exception failure) {
                // 정상 반환의 조건은 하위 작업 종료(또는 배포 권한 격리)다.
                adapter.cancelAndAwait(child)
                throw failure
            }
            if (status != 0) {
                String result = [2: 'RECOVERED', 3: 'DRAIN_TIMEOUT'].get(status, 'FAILED')
                return [result: result, role: role, status: status]
            }
        }
        return [result: 'SUCCESS']
    }
}

def consume(String role, Map release, Map actual, Closure deploy) {
    if (!(role in ['api', 'collector', 'analyst'])) {
        throw new IllegalArgumentException('알 수 없는 배포 역할')
    }
    // Jenkins 호출자는 결과를 신뢰할 수 있는 상위 잡 실행인지 직접 확인한다.
    if (actual.trustedParent != true) {
        throw new IllegalArgumentException('신뢰할 수 있는 부모 잡 요청이 필요하다')
    }
    validateRelease(release)
    for (String field in ['revision', 'imageId', 'agent', 'daemon']) {
        if (actual[field] != release[field]) {
            throw new IllegalArgumentException("배포 인계 불일치: ${field}")
        }
    }
    return deploy(role, release.revision, release.imageId)
}

void validateRelease(Map release) {
    if (!(release.revision instanceof String) || !(release.revision ==~ /[0-9a-f]{40}/) ||
        !(release.imageId instanceof String) || !(release.imageId ==~ /sha256:[0-9a-f]{64}/) ||
        !(release.agent instanceof String) || !(release.agent ==~ /[a-zA-Z0-9_.-]{1,128}/) ||
        !(release.daemon instanceof String) || !(release.daemon ==~ /[a-zA-Z0-9_.:-]{1,128}/)) {
        throw new IllegalArgumentException('준비된 배포 인계 값이 유효하지 않다')
    }
}

return this
