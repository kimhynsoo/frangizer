<script setup>
import { computed, ref } from "vue";
import { ChevronDown, Search } from "@lucide/vue";
import { faqItems } from "../faq";
const search = ref("");
const groups = computed(() => {
  const matches = faqItems.filter(
    (item) =>
      item.question.includes(search.value.trim()) ||
      item.category.includes(search.value.trim()),
  );
  return [...new Set(matches.map((item) => item.category))].map((category) => ({
    category,
    items: matches.filter((item) => item.category === category),
  }));
});
</script>
<template>
  <section class="faq-page">
    <div class="page-heading">
      <div>
        <span class="eyebrow">FAQ</span>
        <h1>자주 묻는 질문</h1>
        <p>가맹을 준비하며 궁금한 내용을 찾아보세요</p>
      </div>
    </div>
    <label class="faq-search"
      ><span><Search :size="16" />질문 검색</span
      ><input
        v-model="search"
        type="search"
        placeholder="비용, 계약, 교육 등으로 검색"
    /></label>
    <section
      v-for="group in groups"
      :key="group.category"
      class="surface faq-group"
      :aria-label="group.category"
    >
      <div class="surface-toolbar">
        <h2>{{ group.category }}</h2>
        <span class="muted">{{ group.items.length }}개 질문</span>
      </div>
      <details v-for="item in group.items" :key="item.id" class="faq-item">
        <summary class="faq-question">
          <span
            ><span class="faq-q" aria-hidden="true">Q.</span
            >{{ item.question }}</span
          >
          <ChevronDown :size="18" aria-hidden="true" />
        </summary>
        <div class="faq-answer">
          <p>{{ item.answer }}</p>
        </div>
      </details>
    </section>
    <p v-if="!groups.length" class="empty-state" role="status">
      검색 결과가 없습니다. 다른 단어로 찾아보세요
    </p>
  </section>
</template>
