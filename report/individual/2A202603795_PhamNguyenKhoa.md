# Báo cáo cá nhân — Day 6

Học viên: Pham Nguyen Khoa (2A202603795, L3B). Lần chạy chính thức: 2026-11-14 03:58 UTC (`python -m src.run`). Mọi số liệu dưới đây lấy từ `starter/results/`.

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:** *USDA Complete Guide to Home Canning*, Agriculture Information Bulletin No. 539, bản sửa đổi 2015, tải từ NCHFP (nchfp.uga.edu). Dùng 5 file: Intro, Guide 1 (nguyên tắc), Guide 2 (trái cây), Guide 3 (cà chua), Guide 4 (rau củ). Đây là ấn phẩm của chính phủ Mỹ, thuộc public domain theo 17 U.S.C. §105. Tổng 132 trang vật lý, **tính 99 trang nội dung**: intro 4, guide01 28, guide02 28, guide03 22, guide04 17. Trang bị loại gồm bìa, lời cảm ơn, mục lục, index, trang trống hoặc chỉ có số trang, và một worksheet để trống; lý do loại từng trang ghi trong `data/manifest.json`. `page` là số trang vật lý trong PDF (bắt đầu từ 1). Nhãn in trên giấy (ví dụ `2-14`) được giữ trong metadata `page_label`. Manifest có sha256 của từng file; code kiểm tra lại sha256 mỗi lần chạy.
- **Chuẩn hóa, lỗi và giới hạn:** Đọc bằng pypdf 6.21.4, từng trang một (`src/ingestion.py`). Các bước chuẩn hóa: xóa header lặp ở đầu trang (tên guide, số guide, nhãn trang); xóa ghi chú của biên tập viên "Ken – these are Guide 2 Tables…" (3 chỗ); thay 1.810 ký tự U+FFFD bằng dấu cách; chuẩn hóa NFKC; nối 60 từ bị ngắt dòng bằng gạch nối, nhưng giữ gạch nối của từ ghép (`boiling-water`, `dial-gauge`, `500-milligram`); nối dòng bị xuống hàng mềm dựa vào dấu cách cuối dòng; giữ mỗi đoạn, mục liệt kê hoặc tiêu đề trên một dòng. Kết quả: 0 trang lỗi, 0 trang rỗng trong số 99 trang được tính; 220.247 ký tự sau chuẩn hóa (225.705 trước chuẩn hóa); ingest mất 6,2 giây. Log ở `results/ingestion_log.json`. **Giới hạn:** (1) bảng thời gian xử lý mất cấu trúc hàng/cột, mỗi ô thành một dòng; (2) một số bảng bị trích xuất hai lần do PDF có nội dung chồng lớp (guide02 p13, p29), và bảng của quả sung xuất hiện ở cả p13 lẫn p14; (3) còn lỗi đọc chữ lẻ tẻ, ví dụ `com` thay cho `corn` (guide04 p12). Không cần OCR vì không có trang scan.
- **Embedding, vector store, metric, lệnh chạy và chi phí:** `BAAI/bge-small-en-v1.5` qua sentence-transformers 6.3.0 (torch 2.15.0), chạy local trên CPU, 384 chiều, tối đa 512 token. Chunk dài nhất 800 ký tự (khoảng 200 token) nên không bị cắt. Query được thêm tiền tố `"Represent this sentence for searching relevant passages: "`, document để nguyên; mọi vector được chuẩn hóa độ dài. Vector store: PostgreSQL 16.16 + pgvector 0.8.7 (Docker, `starter/docker-compose.yml`), một bảng `chunks` với khóa chính `(strategy, chunk_id)`. Metric cosine (`<=>`), **exact search** (không có index ANN). Lệnh chạy: `python -m src.run` rồi `python -m src.audit`, đặt trong thư mục `starter/`. Thời gian embed: A 14,5 giây, B 13,75 giây; độ trễ search trung vị 30,5 ms. **Chi phí: 0 USD.**
- **Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:** Mỗi chunk có `document_id`, `source` (tên file PDF), `page` (int), `page_label`. Trường lọc là **`food_category`** (text: `general`, `principles`, `fruits`, `tomatoes`, `vegetables`), lấy từ guide mà trang thuộc về, ghi trong manifest, không suy đoán. Lý do chọn: corpus chia tự nhiên theo nhóm thực phẩm, và nhiều câu hỏi chứa từ dễ kéo kết quả sang nhóm khác (ví dụ "green tomato pie filling" nằm trong guide trái cây). B và C có thêm `section`, là tiêu đề gần nhất trên trang.

## Cấu hình và benchmark

- **A / B / C:**
  - **A:** `fixed_size`, 500 ký tự, overlap 50, trong phạm vi từng trang, ký tự xuống dòng thay bằng dấu cách. Không filter.
  - **B:** `by_paragraph`, tối đa 800 ký tự, trong phạm vi từng trang. Tiêu đề (dòng VIẾT HOA, hoặc dòng ngắn theo sau là đoạn ≥150 ký tự) luôn mở chunk mới. Dòng dài hơn 800 ký tự được cắt theo câu. Chunk dưới 100 ký tự được gộp vào chunk bên cạnh. Không filter.
  - **C:** dùng đúng chunk và index của B, chỉ thêm `WHERE food_category = …` cho 5 câu hỏi có `filter`; 15 câu còn lại chạy không filter.
  - **Khác biệt duy nhất giữa B và C là filter. Khác biệt duy nhất giữa A và B là cách chia chunk.** Cả ba dùng chung corpus, model, store, metric và top-k = 3.
- **≥20 câu:** 20 câu (`benchmark/questions.json`), chia **15 dev / 5 test** theo chủ đề, chốt trước khi chạy và commit trong `1b51f16` trước lần search đầu tiên. Loại câu: trực tiếp 4, diễn đạt lại 3, có điều kiện/ngoại lệ 6, metadata filter 5 (dev q11–q13, test q18–q19), đáp án trong bảng 2. Evidence được copy từ text đã chuẩn hóa và kiểm tra tự động bằng `scripts/verify_benchmark.py`: mỗi câu trích phải có thật trên trang gold, giá trị filter phải tồn tại, và trang gold phải nằm trong phạm vi filter. Tác giả đọc lại từng trang nguồn để xác nhận đáp án. Ba câu có nhiều nguồn hợp lệ, ghi trong `alternative_gold`.
- **Tiêu chí chọn chiến lược đã chốt trước khi xem test** (`benchmark/README.md`): dev Hit@3, rồi dev Hit@1, rồi ít chunk hơn, rồi thời gian embed thấp hơn.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian (embed) | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 10/15 | 12/15 | 533 | 14,5 s | 0 |
| A | test | 5/5 | 5/5 | 533 | | 0 |
| B | dev | 5/15 | 12/15 | 406 | 13,75 s | 0 |
| B | test | 2/5 | 2/5 | 406 | | 0 |
| C | dev | 6/15 | **13/15** | 406 (dùng chung index B) | 13,75 s | 0 |
| C | test | 2/5 | 2/5 | 406 | | 0 |

Chỉ số phụ, đúng trang ở top-3 bất kể evidence: A 13/15 dev và 5/5 test; B 13/15 và 3/5; C 14/15 và 3/5.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 0,8 (10) | 15/15 | test 1/2 (không áp filter); dev 1/3 | 0/0/5, trung bình 2,0 |
| B | 1,7 (10) | 15/15 | test 1/2 (không áp filter); dev 1/3 | 0/2/3, trung bình 1,6 |
| C | 1,7 (10, chung chunk với B) | 15/15 | **test 2/2; dev 3/3** | 0/2/3, trung bình 1,6 |

**Đường dẫn trace, nhãn thủ công và cách chọn mẫu:**
- `starter/results/traces.jsonl`: 60 trace (3 cấu hình × 20 câu). Mỗi trace gồm top-3 với chunk_id, text, score, metadata và cờ hit.
- `results/metrics.json` (chỉ số và quyết định chọn), `results/config.json` (tham số, phiên bản thư viện, thời gian).
- Coherence: `results/audit/coherence_samples.csv`. Mẫu là 10 trang đầu sau khi sắp xếp theo `(document_id, page)`, vị trí chấm là ký tự ở giữa trang. Quy tắc này chốt trước khi xem điểm.
- Grounding và metadata: `results/audit/test_top3_records.csv` và `results/audit/metadata_checks.csv`.
- Nhãn và lý do: `results/audit/manual_labels.json`. Tổng hợp: `results/audit/summary.json`.
- Metadata được kiểm tra bằng script: chunk text có thật trên trang ghi trong metadata, `source` và `food_category` khớp manifest, `page_label` khớp header của trang, `section` là một dòng có thật trên trang.
- **Người chấm:** nhãn coherence và grounding do Claude (Claude Code) chấm nháp bằng cách đọc từng chunk, độc lập với cờ Hit tự động; tác giả duyệt lại. Rủi ro thiên lệch: người chấm biết chunk thuộc cấu hình nào.

## Phân tích và kết luận

- **So sánh A–B (thay đổi cách chia chunk):** B giữ trọn ý tốt hơn nhiều (coherence 1,7 so với 0,8). Cả 10/10 chunk A trong mẫu đều bị cắt giữa từ hoặc giữa câu ở đầu hoặc cuối; 2 chunk mất hẳn chủ ngữ (pos02, pos07) nên chấm 0. B dùng ít chunk hơn (406 so với 533) và tổng ký tự không bị phình do overlap (219.940 so với 241.769). Nhưng **A thắng rõ ở Hit@1** (dev 10/15 so với 5/15), và Hit@3 dev bằng nhau (12/15). Giải thích hợp lý nhất: chunk 500 ký tự tập trung vào ít câu hơn, nên câu chứa đáp án nổi bật hơn trong embedding. Chunk tối đa 800 ký tự của B pha thêm câu xung quanh làm điểm tương đồng bị "loãng".
- **So sánh B–C (thay đổi filter):** filter chỉ có tác dụng trên câu có filter. C tuân thủ filter 3/3 (dev) và 2/2 (test), trong khi B chỉ 1/3 và 1/2. Ví dụ ở q19: top-3 của B lẫn một chunk về dưa muối từ guide nguyên tắc. Filter còn cứu được **q13** ("Can I mash pumpkin…", gold guide04 p18): B không có trang đúng trong top-3, còn C có hit ngay hạng 1.
- **Ví dụ lỗi 1, q07 (dev, guide01 p22):** A trượt cả Hit@3 vì evidence dài khoảng 200 ký tự ("If at any time pressure goes below the recommended amount…") bị chia ra hai chunk 500 ký tự. Đây là giới hạn cấu trúc của cách chia cố định: chỉ có 14/15 evidence dev nằm trọn trong một chunk A, so với 15/15 của B.
- **Ví dụ lỗi 2, q16 (test, guide01 p15):** B và C trượt. Mục "Sterilization of empty jars" bắt đầu ở cuối p14 và kết thúc ở đầu p15, nhưng chunk không được vượt qua ranh giới trang. Chunk p15 của B bắt đầu giữa câu và mất tiêu đề, nên chunk p14 (chỉ nói về thực phẩm xử lý dưới 10 phút) xếp hạng cao hơn. A tìm đúng câu ở p15 tại hạng 1. q17 (guide01 p25) cũng tương tự: chunk hạng 1 của B dừng đúng ở "test seals with one of the following options:", còn các lựa chọn nằm ở chunk sau.
- **Ví dụ lỗi 3, gold sót nguồn, q20 (test):** bảng thời gian xử lý quả sung được in ở **cả guide02 p13 lẫn p14** (PDF chồng lớp), nhưng gold đã chốt trước chỉ ghi p14. Hạng 2 của B và C (guide02 p13) chứa đầy đủ bảng, nên nhãn grounding thủ công là 2, trong khi Hit@3 tự động tính là trượt. Chỉ số chính thức được **giữ nguyên** vì không sửa bộ test sau khi xem kết quả. Phân tích hậu kiểm: nếu chấp nhận p13 thì test Hit@3 của B và C là 3/5.
- **Chiến lược chọn, đánh đổi, điều kiện áp dụng:** theo tiêu chí đã chốt, **C được chọn trên dev** (Hit@3 13/15, cao nhất). **Kết quả test không xác nhận lựa chọn này**: A đạt 5/5 cả Hit@1 và Hit@3, C đạt 2/5. Tác giả không đổi lựa chọn sau khi xem test, vì làm vậy là tinh chỉnh trên test. Kết luận trung thực: trên corpus này, **A tốt hơn về độ chính xác truy xuất ở hạng 1, còn B/C tốt hơn về độ mạch lạc của chunk và việc tuân thủ phạm vi**. Filter của C là cải tiến rõ ràng khi người dùng biết trước nhóm thực phẩm. Nếu mục tiêu là đưa thẳng top-1 cho người đọc, nên dùng chunk nhỏ như A. Nếu chunk được đưa vào LLM để sinh câu trả lời và cần ngữ cảnh trọn vẹn, B/C cho grounding đủ ý hơn khi không vấp ranh giới trang.
- **Hạn chế:** test chỉ có 5 câu, nên một câu đổi kết quả là thay đổi 20 điểm phần trăm. Không thể kết luận cấu hình nào tốt nhất một cách tổng quát. Gold sót một nguồn hợp lệ (q20). Nhãn thủ công do một người chấm, biết trước cấu hình. Ranh giới trang cắt các mục kéo dài sang trang sau. Bảng bị vỡ cấu trúc. Thời gian đo trên một lần chạy, một máy (Windows 11, CPU), nên chỉ mang tính tham khảo. A và B khác nhau đồng thời ở kích thước (500 so với 800) và cách cắt, nên không tách riêng được hai yếu tố này.
- **Nếu làm lại hoặc mở rộng:**
  1. Cho phép chunk B nối sang trang sau nhưng vẫn lưu cả hai số trang.
  2. Thử B với tối đa 500 ký tự, để tách ảnh hưởng của kích thước khỏi ảnh hưởng của cách cắt.
  3. Trích bảng thành dạng hàng có nhãn, ví dụ "Figs, hot pack, pints, 0–1,000 ft: 45 min".
  4. Khi viết gold, tìm mọi trang chứa cùng evidence.
  5. Thử hybrid với BM25 cho câu hỏi về bảng và số liệu.

**Công cụ AI:** dùng Claude Code để giải thích, viết code, soạn bản nháp câu hỏi và nhãn chấm. Cách kiểm tra: chạy lại pipeline, dùng script đối chiếu evidence, và tác giả đọc lại từng trang nguồn cùng từng nhãn.
