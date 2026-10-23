package com.frangizer.auth;

import java.util.Arrays;
import java.util.List;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;

import com.frangizer.common.ApiException;

@Service
public class AuthService {

    private static final Logger log = LoggerFactory.getLogger(AuthService.class);
    private static final String USER_SQL = """
            SELECT u.id, u.username, u.role, u.brand_id, u.store_id, s.region_code, u.permissions, u.password_hash
            FROM users u LEFT JOIN stores s ON s.store_id = u.store_id
            """;

    private final JdbcClient db;
    private final BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();
    // 없는 계정도 같은 시간이 걸리도록 비교용 해시
    private final String dummyHash = encoder.encode("dummy-password");
    private final String demoPassword;

    AuthService(JdbcClient db, @Value("${app.demo-password}") String demoPassword) {
        this.db = db;
        this.demoPassword = demoPassword;
    }

    private record Row(AuthUser user, String passwordHash) {
    }

    private Optional<Row> load(String where, String value) {
        return db.sql(USER_SQL + where).param("v", value)
                .query((rs, n) -> new Row(new AuthUser(rs.getString("id"), rs.getString("username"),
                        rs.getString("role"), rs.getString("brand_id"), rs.getString("store_id"),
                        rs.getString("region_code"), split(rs.getString("permissions"))),
                        rs.getString("password_hash")))
                .optional();
    }

    private static List<String> split(String csv) {
        return csv == null || csv.isBlank() ? List.of() : Arrays.stream(csv.split(",")).map(String::trim).toList();
    }

    public Optional<AuthUser> findById(String id) {
        return load("WHERE u.id = :v", id).map(Row::user);
    }

    public AuthUser authenticate(String username, String password) {
        Optional<Row> row = load("WHERE u.username = :v", username);
        String hash = row.map(Row::passwordHash).orElse(null);
        boolean ok = encoder.matches(password, hash != null ? hash : dummyHash) && hash != null;
        if (!ok) throw new ApiException(HttpStatus.UNAUTHORIZED, "AUTH_FAILED", "아이디 또는 비밀번호가 올바르지 않습니다.");
        return row.get().user();
    }

    /** 시연 계정(demo-*) 비밀번호를 환경변수로 주입한다. 저장소에는 비밀번호를 두지 않는다. */
    @EventListener(ApplicationReadyEvent.class)
    void seedDemoPasswords() {
        if (demoPassword == null || demoPassword.isBlank()) return;
        int n = db.sql("UPDATE users SET password_hash = :h WHERE username LIKE 'demo-%' AND password_hash IS NULL")
                .param("h", encoder.encode(demoPassword)).update();
        if (n > 0) log.info("demo accounts password set: {}", n);
    }
}
