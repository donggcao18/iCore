# 1. Generate test call trees
python -m scripts.test_retrieval.similarities.get_all_cg_parallel \
    --output_dir ./retrieval_results/swe_test_cgs/ \
    --swt

# 2. Perform initial test retrieval
python -m scripts.test_retrieval.initial_retrieval \
    --related_tests_path ./retrieval_results/test/related_tests_1.json \
    --swt

for i in $(seq 1 3); do
    # 3. Generate test cases
    python -m scripts.generator.llm_query \
    --exp_name sketch \
    --query_time 1 \
    --context_code_path ./retrieval_results/code/retrieval_results.json \
    --context_test_path ./retrieval_results/test/related_tests_${i}.json \
    --out_dir ./data/sketch/gen_tests_gpt-4o_${i}/ \
    --template_file ./data/prompt_templates/prompt_with_code_and_tests.json \
    --model gpt-4o \
    --temperature 0.0 \
    --swt
    
    # 4. Calculate similarity scores
    python -m scripts.test_retrieval.retrieve_test \
    --gen_test_dir ./data/sketch/gen_tests_gpt-4o_1 \
    --output_dir ./retrieval_results/test/test_similarity/${i}/ \
    --injection_path ./retrieval_results/test/related_tests_${i}.json \
    --tree_path ./retrieval_results/swe_test_cgs/ \
    --keywords_path ./retrieval_results/code/keywords_gpt-4o.json \
    --swt

    # 5. Rerank candidate tests
    python -m scripts.test_retrieval.rerank \
    --output_related_tests_path ./retrieval_results/test/related_tests_${i+1}.json \
    --message_dir ./retrieval_results/test/messages/messages_2/ \
    --test_similarity_dir ./retrieval_results/test/test_similarity/${i}/ \
    --last_related_tests_path ./retrieval_results/test/related_tests_${i}.json \
    --swt

done
