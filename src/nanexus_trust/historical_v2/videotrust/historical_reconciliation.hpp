#pragma once
// Internal test seam: only reports captured by a successful generating Feed call
// enter this reconciler in production. Summary snapshots are never inputs.
#include "videotrust/historical_observation.hpp"
#include <map>
#include <string_view>
namespace videotrust::historical_detail {
struct Snapshot {
  std::string_view types, statuses;
  uint64_t received{0}, validated{0};
  HistoricalAxes axes;
  bool accumulated_key_changed{false};
};
class Ledger {
 public:
  Ledger(std::string run, HistoricalLimits caps) : run_(std::move(run)), caps_(caps) {}
  Error CanAppend(std::string_view token) const;
  Error Append(std::string token);
  Expected<HistoricalUpdate> Reconcile(const Snapshot& report, bool trust_configured);
  HistoricalUpdate Finish();
  std::size_t size() const { return active_.size(); }
  uint64_t consumed() const { return consumed_; }
 private:
  std::string run_;
  HistoricalLimits caps_;
  std::map<uint64_t, HistoricalItem> active_;
  uint64_t consumed_{0}, generation_{0}, retired_count_{0}, emitted_{0};
  bool key_changed_{false};
};
bool ValidLimits(const HistoricalLimits& limits);
} // namespace videotrust::historical_detail
