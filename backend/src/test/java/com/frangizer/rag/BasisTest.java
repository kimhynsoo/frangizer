package com.frangizer.rag;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.time.OffsetDateTime;

import org.junit.jupiter.api.Test;

class BasisTest {

    // 현재 시각 고정: 2026-10-23 13:00 KST
    static final Instant NOW = OffsetDateTime.parse("2026-10-23T13:00:00+09:00").toInstant();

    static Instant kst(String t) {
        return OffsetDateTime.parse(t + "+09:00").toInstant();
    }

    @Test
    void sameDayBoundaries() {
        assertThat(RagService.basis(NOW, NOW)).isEqualTo("current");
        assertThat(RagService.basis(kst("2026-10-23T23:59:59.999999999"), NOW)).isEqualTo("future");
        assertThat(RagService.basis(kst("2026-10-23T16:00:00"), NOW)).isEqualTo("future");
        assertThat(RagService.basis(kst("2026-10-23T00:00:00"), NOW)).isEqualTo("past");
    }

    @Test
    void otherDays() {
        assertThat(RagService.basis(kst("2026-10-22T23:59:59"), NOW)).isEqualTo("past");
        assertThat(RagService.basis(kst("2026-10-26T00:00:00"), NOW)).isEqualTo("future");
    }
}
