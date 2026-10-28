#pragma once

#include "videotrust/error.hpp"
#include "videotrust/result.hpp"
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace videotrust {
// Independent Historical observation API. All identity/revision numbers below
// are adapter-local, not durable upstream identities. No host/time claims.
enum class HistoricalSigningState {
  VerifiedSigning, IntegrityNegative, NoRecognizedSigningEvidence, Indeterminate
};
enum class HistoricalStop { None, ResourcePolicy, InputQualification, BackendFailure, Finished };
struct HistoricalLimits {
  std::size_t max_nal_bytes{1024 * 1024};
  std::size_t max_active_items{4096};
  std::size_t max_report_entries{8192}; // Includes synthetic entries.
  std::size_t max_source_token_bytes{256};
  uint64_t max_feeds{1000000};
  uint64_t max_emitted_items{2000000};
};
struct HistoricalAxes {
  int authenticity{0};
  int provenance{0};
  int combined{0};
  bool key_changed{false};
  std::optional<int64_t> raw_signing_start;
  std::optional<int64_t> raw_signing_end;
};
struct HistoricalItem {
  uint64_t received_ordinal{0};
  std::string source_token; // Opaque; never an authenticated source/time binding.
  char nalu_type{'U'};
  char raw_status{'P'};
  uint64_t revision{0}; // Raw status/type changes between fresh reports only.
  uint64_t report_generation{0};
  bool finalized{false}; // Retired from reconciliation, not unconditional trust finality.
  // Negative means membership in the validator-affected set, never that each
  // item/frame was individually edited (especially in low-bitrate mode).
  HistoricalSigningState state{HistoricalSigningState::Indeterminate};
  HistoricalAxes axes;
};
struct HistoricalMissing {
  std::optional<uint64_t> left_received;
  std::optional<uint64_t> right_received;
  uint64_t report_generation{0};
  // One synthetic placeholder. No received ordinal, source token, PTS or duration.
};
struct HistoricalUpdate {
  std::string run_id;
  uint64_t generating_feed_ordinal{0};
  uint64_t report_generation{0};
  bool fresh_report{false};
  std::optional<uint64_t> retired_prefix_ordinal;
  std::vector<HistoricalItem> items;
  std::vector<HistoricalMissing> missing;
};

class HistoricalExtractor {
 public:
  static Expected<HistoricalExtractor> Create(Codec codec, std::string run_id,
                                               HistoricalLimits limits = {});
  HistoricalExtractor(HistoricalExtractor&&) noexcept;
  HistoricalExtractor& operator=(HistoricalExtractor&&) noexcept;
  ~HistoricalExtractor();
  HistoricalExtractor(const HistoricalExtractor&) = delete;
  HistoricalExtractor& operator=(const HistoricalExtractor&) = delete;
  // Configure once, before feeding. Empty PEM leaves trust unavailable.
  Error SetTrustedCertificate(std::string_view pem);
  // Exactly one complete Annex-B NAL per call. No file EOF/reset operation.
  // Nonfresh calls return no positional items. No summary-fetch path exists.
  Expected<HistoricalUpdate> Feed(std::span<const uint8_t> nal, std::string source_token);
  // Terminal; unresolved items remain provisional/indeterminate. Does not create
  // a validator EOF signature, replay, or no-recognized-evidence observation.
  HistoricalUpdate Finish();
  HistoricalStop stop_reason() const noexcept;
  uint64_t consumed_feeds() const noexcept;
  std::size_t active_items() const noexcept;
 private:
  struct Impl;
  explicit HistoricalExtractor(std::unique_ptr<Impl> impl);
  std::unique_ptr<Impl> impl_;
};
} // namespace videotrust
