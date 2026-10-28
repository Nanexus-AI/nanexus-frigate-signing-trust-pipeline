#include "videotrust/historical_observation.hpp"
#include "historical_reconciliation.hpp"
#include "videotrust/session.hpp"
#include <onvif_media_signing_validator.h>
#include <algorithm>
#include <cstring>
#include <limits>
#include <utility>

namespace videotrust {
namespace historical_detail {
namespace {
Error Invalid() { return MakeError(ErrorCode::ParseError, "historical: unreconciled report"); }
bool Video(char type) { return type=='I'||type=='i'||type=='P'||type=='p'; }
HistoricalSigningState State(const HistoricalItem& item, bool trust, bool key_changed) {
  const auto& a=item.axes;
  if (!item.finalized || key_changed || a.authenticity==OMS_AUTHENTICITY_VERSION_MISMATCH)
    return HistoricalSigningState::Indeterminate;
  if (item.raw_status=='N' && Video(item.nalu_type) && a.authenticity==OMS_AUTHENTICITY_NOT_OK)
    return HistoricalSigningState::IntegrityNegative;
  if (item.raw_status=='.' && Video(item.nalu_type) && trust &&
      a.provenance==OMS_PROVENANCE_OK &&
      (a.combined==OMS_AUTHENTICITY_AND_PROVENANCE_OK || a.combined==OMS_AUTHENTICITY_AND_PROVENANCE_OK_WITH_MISSING_INFO) &&
      (a.authenticity==OMS_AUTHENTICITY_OK || a.authenticity==OMS_AUTHENTICITY_OK_WITH_MISSING_INFO))
    return HistoricalSigningState::VerifiedSigning;
  return HistoricalSigningState::Indeterminate;
}
} // namespace
bool ValidLimits(const HistoricalLimits& c) {
  // Safety ceilings, not deployment qualification or a universal pending bound.
  return c.max_nal_bytes>0 && c.max_nal_bytes<=16*1024*1024 &&
    c.max_active_items>0 && c.max_active_items<=65536 &&
    c.max_report_entries>0 && c.max_report_entries<=131072 &&
    c.max_source_token_bytes>0 && c.max_source_token_bytes<=1024 &&
    c.max_feeds>0 && c.max_feeds<std::numeric_limits<unsigned int>::max() &&
    c.max_emitted_items>0 && c.max_emitted_items<=100000000;
}
Error Ledger::CanAppend(std::string_view token) const {
  if (consumed_>=caps_.max_feeds || active_.size()>=caps_.max_active_items)
    return MakeError(ErrorCode::NotSupported,"historical: resource policy");
  if (token.empty() || token.size()>caps_.max_source_token_bytes)
    return MakeError(ErrorCode::InvalidArgument,"historical: bounded source token required");
  return MakeError(ErrorCode::Ok,{});
}
Error Ledger::Append(std::string token) {
  const auto check=CanAppend(token); if (check.code!=ErrorCode::Ok) return check;
  HistoricalItem item; item.received_ordinal=consumed_; item.source_token=std::move(token);
  active_.emplace(consumed_++,std::move(item));
  return MakeError(ErrorCode::Ok,{});
}
Expected<HistoricalUpdate> Ledger::Reconcile(const Snapshot& r, bool trust) {
  if (r.types.size()!=r.statuses.size() || r.types.empty() || r.received!=consumed_ || active_.empty()) return Invalid();
  if (r.types.size()>caps_.max_report_entries)
    return MakeError(ErrorCode::NotSupported,"historical: report cap");
  std::size_t received=0;
  for (std::size_t i=0;i<r.types.size();++i) {
    if (std::string_view("IiPpCSszv_oU ").find(r.types[i])==std::string_view::npos ||
        std::string_view("PUNEM_.").find(r.statuses[i])==std::string_view::npos) return Invalid();
    if ((r.types[i]==' ') != (r.statuses[i]=='M')) return Invalid();
    if (r.types[i]!=' ') ++received;
  }
  if (received!=active_.size() || received>consumed_) return Invalid();
  const uint64_t first=consumed_-received;
  if (first!=retired_count_ || active_.begin()->first!=first) return Invalid();
  if (r.types.size()>caps_.max_emitted_items-emitted_)
    return MakeError(ErrorCode::NotSupported,"historical: output cap");
  HistoricalUpdate out; out.run_id=run_; out.fresh_report=true;
  out.generating_feed_ordinal=consumed_-1; out.report_generation=++generation_;
  key_changed_ |= r.axes.key_changed || r.accumulated_key_changed;
  uint64_t next=first; bool pending=false;
  for (std::size_t i=0;i<r.types.size();++i) {
    if (r.types[i]==' ') {
      HistoricalMissing m; m.report_generation=generation_;
      if (next>0) m.left_received=next-1;
      if (next<consumed_) m.right_received=next;
      out.missing.push_back(m); continue;
    }
    auto& item=active_.at(next++);
    if (item.report_generation && (item.raw_status!=r.statuses[i] || item.nalu_type!=r.types[i])) ++item.revision;
    item.raw_status=r.statuses[i]; item.nalu_type=r.types[i];
    item.axes=r.axes; item.report_generation=generation_;
    pending |= item.raw_status=='P'; item.finalized=!pending;
    item.state=State(item,trust,key_changed_);
    out.items.push_back(item);
  }
  uint64_t retiring=0;
  for (const auto& item:out.items) { if (!item.finalized) break; ++retiring; }
  if (r.validated!=retired_count_+retiring) return Invalid();
  for (uint64_t i=0;i<retiring;++i) active_.erase(retired_count_++);
  if (retired_count_) out.retired_prefix_ordinal=retired_count_-1;
  emitted_+=r.types.size();
  return out;
}
HistoricalUpdate Ledger::Finish() {
  HistoricalUpdate out; out.run_id=run_; out.report_generation=generation_;
  if (consumed_) out.generating_feed_ordinal=consumed_-1;
  if (retired_count_) out.retired_prefix_ordinal=retired_count_-1;
  for (auto& [ordinal,item]:active_) {
    (void)ordinal; item.finalized=false; item.state=HistoricalSigningState::Indeterminate;
    out.items.push_back(std::move(item));
  }
  active_.clear(); return out;
}
} // namespace historical_detail

struct HistoricalExtractor::Impl {
  std::optional<MediaSigningSession> session;
  std::string run;
  HistoricalLimits caps;
  historical_detail::Ledger ledger;
  HistoricalStop stop{HistoricalStop::None};
  bool trust{false}, trust_set{false};
  Impl(MediaSigningSession s, HistoricalLimits c, std::string run)
      :session(std::move(s)),run(run),caps(c),ledger(std::move(run),c) {}
  Error Fail(Error e, HistoricalStop reason) {
    stop=reason;
    session.reset(); // Terminal release, never reset/replay to continue.
    return e;
  }
};
HistoricalExtractor::HistoricalExtractor(std::unique_ptr<Impl> p):impl_(std::move(p)){}
HistoricalExtractor::HistoricalExtractor(HistoricalExtractor&&) noexcept=default;
HistoricalExtractor& HistoricalExtractor::operator=(HistoricalExtractor&&) noexcept=default;
HistoricalExtractor::~HistoricalExtractor()=default;
Expected<HistoricalExtractor> HistoricalExtractor::Create(Codec codec,std::string run,HistoricalLimits limits) {
  if ((codec!=Codec::H264 && codec!=Codec::H265) || run.empty() || run.size()>256 || !historical_detail::ValidLimits(limits))
    return MakeError(ErrorCode::InvalidArgument,"historical: invalid configuration");
  auto s=MediaSigningSession::Create(codec); if (!s.ok()) return s.error();
  return HistoricalExtractor(std::make_unique<Impl>(std::move(s.value()),limits,std::move(run)));
}
Error HistoricalExtractor::SetTrustedCertificate(std::string_view pem) {
  if (impl_->stop!=HistoricalStop::None || impl_->ledger.consumed() || impl_->trust_set)
    return MakeError(ErrorCode::InvalidArgument,"historical: trust must be fixed before feed");
  impl_->trust_set=true;
  if (pem.size()>32768) return impl_->Fail(MakeError(ErrorCode::InvalidArgument,"historical: trust size cap"),HistoricalStop::InputQualification);
  if (pem.empty()) return MakeError(ErrorCode::Ok,{});
  const auto rc=onvif_media_signing_set_trusted_certificate(impl_->session->get(),pem.data(),pem.size());
  if (rc!=OMS_OK) return impl_->Fail(MakeError(ErrorCode::UpstreamFailure,"historical: trust configuration failed"),HistoricalStop::BackendFailure);
  impl_->trust=true; return MakeError(ErrorCode::Ok,{});
}
Expected<HistoricalUpdate> HistoricalExtractor::Feed(std::span<const uint8_t> nal,std::string token) {
  if (impl_->stop!=HistoricalStop::None) return MakeError(ErrorCode::InvalidArgument,"historical: terminal run");
  if (nal.size()>impl_->caps.max_nal_bytes)
    return impl_->Fail(MakeError(ErrorCode::NotSupported,"historical: NAL cap"),HistoricalStop::ResourcePolicy);
  const bool start=nal.size()>=4 && nal[0]==0 && nal[1]==0 &&
    (nal[2]==1 || (nal.size()>=5 && nal[2]==0 && nal[3]==1));
  if (!start) return impl_->Fail(MakeError(ErrorCode::InvalidArgument,"historical: complete Annex-B NAL required"),HistoricalStop::InputQualification);
  const std::size_t prefix=nal[2]==1?3:4;
  for (std::size_t i=prefix;i+2<nal.size();++i) {
    if (nal[i]==0 && nal[i+1]==0 && nal[i+2]==1)
      return impl_->Fail(MakeError(ErrorCode::InvalidArgument,"historical: multiple NALs in one feed"),HistoricalStop::InputQualification);
  }
  auto appended=impl_->ledger.CanAppend(token);
  if (appended.code!=ErrorCode::Ok) return impl_->Fail(appended,appended.code==ErrorCode::NotSupported?HistoricalStop::ResourcePolicy:HistoricalStop::InputQualification);
  onvif_media_signing_authenticity_t* raw=nullptr;
  const auto rc=onvif_media_signing_add_nalu_and_authenticate(impl_->session->get(),nal.data(),nal.size(),&raw);
  std::unique_ptr<onvif_media_signing_authenticity_t, decltype(&onvif_media_signing_authenticity_report_free)> report(raw,onvif_media_signing_authenticity_report_free);
  if (rc!=OMS_OK) return impl_->Fail(MakeError(ErrorCode::UpstreamFailure,"historical: feed failed; attribution stopped"),HistoricalStop::BackendFailure);
  impl_->ledger.Append(std::move(token));
  if (!report) { HistoricalUpdate out; out.run_id=impl_->run; out.generating_feed_ordinal=impl_->ledger.consumed()-1; return out; }
  const auto& latest=report->latest_validation;
  if (!latest.nalu_str || !latest.validation_str) return impl_->Fail(MakeError(ErrorCode::ParseError,"historical: missing report strings"),HistoricalStop::InputQualification);
  const auto cap=impl_->caps.max_report_entries;
  const auto types=strnlen(latest.nalu_str,cap+1),statuses=strnlen(latest.validation_str,cap+1);
  if (types>cap || statuses>cap) return impl_->Fail(MakeError(ErrorCode::NotSupported,"historical: report cap"),HistoricalStop::ResourcePolicy);
  HistoricalAxes axes; axes.authenticity=latest.authenticity; axes.provenance=latest.provenance;
  axes.combined=latest.authenticity_and_provenance; axes.key_changed=latest.public_key_has_changed;
  if (latest.start_timestamp) axes.raw_signing_start=latest.start_timestamp;
  if (latest.end_timestamp) axes.raw_signing_end=latest.end_timestamp;
  historical_detail::Snapshot snapshot{{latest.nalu_str,types},{latest.validation_str,statuses},
    report->accumulated_validation.number_of_received_nalus,report->accumulated_validation.number_of_validated_nalus,
    axes,report->accumulated_validation.public_key_has_changed};
  auto result=impl_->ledger.Reconcile(snapshot,impl_->trust);
  if (!result.ok()) return impl_->Fail(result.error(),result.error().code==ErrorCode::NotSupported?HistoricalStop::ResourcePolicy:HistoricalStop::InputQualification);
  return result;
}
HistoricalUpdate HistoricalExtractor::Finish() {
  if (impl_->stop==HistoricalStop::Finished) return {};
  auto out=impl_->ledger.Finish();
  impl_->session.reset();
  if (impl_->stop==HistoricalStop::None) impl_->stop=HistoricalStop::Finished;
  return out;
}
HistoricalStop HistoricalExtractor::stop_reason() const noexcept {return impl_->stop;}
uint64_t HistoricalExtractor::consumed_feeds() const noexcept {return impl_->ledger.consumed();}
std::size_t HistoricalExtractor::active_items() const noexcept {return impl_->ledger.size();}
} // namespace videotrust
