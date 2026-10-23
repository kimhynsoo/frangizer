package com.frangizer;

import java.util.TimeZone;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class FrangizerApplication {

    public static void main(String[] args) {
        // DB DATETIME 컬럼은 UTC로 읽고 쓴다. KST는 응답·집계 경계에서만 쓴다.
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));
        SpringApplication.run(FrangizerApplication.class, args);
    }
}
