package com.frangizer.common;

import java.util.Map;

import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class HealthController {

    private final JdbcClient db;

    HealthController(JdbcClient db) {
        this.db = db;
    }

    @GetMapping("/api/v1/health")
    ResponseEntity<Map<String, String>> health() {
        try {
            db.sql("SELECT 1").query(Integer.class).single();
            return ResponseEntity.ok(Map.of("status", "ready"));
        } catch (RuntimeException e) {
            return ResponseEntity.status(503).body(Map.of("status", "not_ready"));
        }
    }
}
