package com.frangizer.question;

import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.frangizer.auth.AuthUser;
import com.frangizer.common.Api;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;

@RestController
@RequestMapping("/api/v1")
public class QuestionController {

    record FeedbackRequest(@NotNull Boolean helpful) {
    }

    private final QuestionService questions;

    QuestionController(QuestionService questions) {
        this.questions = questions;
    }

    @GetMapping("/conversations/{conversationId}")
    Map<String, Object> conversation(@RequestAttribute(AuthUser.ATTR) AuthUser user, @PathVariable String conversationId) {
        return Api.ok(questions.conversation(user, conversationId));
    }

    @PutMapping("/questions/{questionId}/feedback")
    Map<String, Object> feedback(@RequestAttribute(AuthUser.ATTR) AuthUser user, @PathVariable String questionId,
                                 @Valid @RequestBody FeedbackRequest req) {
        return Api.ok(questions.feedback(user, questionId, req.helpful()));
    }
}
