package com.frangizer.document.dto;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

import org.hibernate.validator.constraints.URL;

import com.fasterxml.jackson.annotation.JsonInclude;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/** POST /documents의 metadata 파트(명세 5.2 UploadMetadata). */
public record UploadMetadata(
        @NotNull @Size(min = 1, max = 200) String title,
        @NotNull @Size(min = 1, max = 50) String version,
        @Pattern(regexp = "^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$") String documentId,
        @NotNull @Pattern(regexp = "franchise|recipe|hygiene|consumer|other") String category,
        @NotNull @Pattern(regexp = "official|public_disclosure|synthetic") String materialType,
        @NotNull Boolean isDemo,
        @NotNull @Pattern(regexp = "prospect|owner|hq") String visibility,
        @NotNull OffsetDateTime effectiveFrom,
        @NotNull @Valid OriginalSource originalSource,
        @NotNull @Size(min = 1, max = 2000) String changeSummary,
        @NotNull @Valid IndexingPolicy indexingPolicy) {

    public record OriginalSource(
            @NotNull @Size(min = 1, max = 200) String title,
            @URL String url,
            @NotNull @Size(min = 1, max = 100) String edition,
            @NotNull @Size(min = 1, max = 100) String publisher,
            @NotNull LocalDate acquiredOn,
            @NotNull @Size(max = 2000) String note) {
    }

    public record IndexingPolicy(
            @JsonInclude(JsonInclude.Include.NON_NULL) @Size(min = 1) List<@NotNull @Min(1) Integer> includePages,
            @NotNull List<@NotNull @Min(1) Integer> excludePages,
            @NotNull Boolean legalTemporalReviewed) {
    }
}
