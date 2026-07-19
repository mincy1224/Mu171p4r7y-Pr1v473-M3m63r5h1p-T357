//  @author  mincy
#include <nanobind/nanobind.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

#include "common.hpp"
#include "bind_common.hpp"
#include "ref_impl/dpf/bgi16.hpp"

namespace nb = nanobind;
using namespace scucse::crypto;

static nb::object dealer_types[32 + 1][8 + 1];
static nb::object eval_types[32 + 1][8 + 1][2];

template <int PARTY> struct DpfParty;

template <> struct DpfParty<0>
{
    static constexpr DpfPid value = DpfPid::EVALUATOR_0;
};

template <> struct DpfParty<1>
{
    static constexpr DpfPid value = DpfPid::EVALUATOR_1;
};

template <uint64_t ELL_IN, uint64_t ELL_OUT> struct DpfDealerT
{
    using DealerType = Bgi16<DpfPid::DEALER, ELL_IN, ELL_OUT, bind::RVECTOR<ELL_OUT>>;

    static constexpr uint64_t ell_in = ELL_IN;
    static constexpr uint64_t ell_out = ELL_OUT;

    DpfDealerT() = default;

    /// Construct with pre-built IOChannels (non-owning raw pointers).
    DpfDealerT(emp::IOChannel* nio0, emp::IOChannel* nio1)
        : dealer_(std::make_unique<DealerType>(nio0, nio1))
    {
    }

    ~DpfDealerT() = default;

    static std::pair<std::string, std::string> gen(uint32_t alpha, uint8_t beta)
    {
        auto [k0, k1] = DealerType::gen(alpha, beta);
        return {nlohmann::json(k0).dump(), nlohmann::json(k1).dump()};
    }

    void sendKey(const std::string& key_json, int party)
    {
        auto key = nlohmann::json::parse(key_json).template get<typename DealerType::KeyType>();
        if (party == 0)
            dealer_->template sendKey<DpfPid::EVALUATOR_0>(key);
        else
            dealer_->template sendKey<DpfPid::EVALUATOR_1>(key);
    }

    void reveal(bind::RVECTOR<ELL_OUT>& out)
    {
        dealer_->reveal(out);
    }

private:
    std::unique_ptr<DealerType> dealer_;
};

template <uint64_t EI, uint64_t EO> static void bind_dpf_dealer(nb::module_& m)
{
    using namespace nb::literals;
    char name[64];
    std::snprintf(name, sizeof(name), "DpfDealer_%lu_%lu", EI, EO);

    nb::class_<DpfDealerT<EI, EO>>(m, name)
        .def(
            "__init__",
            [](DpfDealerT<EI, EO>* self, nb::object eval0_channel, nb::object eval1_channel) {
                auto* nio0 = reinterpret_cast<emp::IOChannel*>(
                    nb::cast<uintptr_t>(eval0_channel.attr("acquire")()));
                auto* nio1 = reinterpret_cast<emp::IOChannel*>(
                    nb::cast<uintptr_t>(eval1_channel.attr("acquire")()));
                new (self) DpfDealerT<EI, EO>(nio0, nio1);
            },
            "eval0_channel"_a, "eval1_channel"_a,
            "Construct a DPF Dealer from persistent channels.\n"
            "  eval0_channel  — Channel to Evaluator 0\n"
            "  eval1_channel  — Channel to Evaluator 1"
        )
        .def_static("gen", &DpfDealerT<EI, EO>::gen, "alpha"_a, "beta"_a,
             "Generate DPF key pair → (key0_json, key1_json)")
        .def("send_key", &DpfDealerT<EI, EO>::sendKey, "key_json"_a, "party"_a,
             "Send a key to evaluator (party=0 or 1)")
        .def("reveal", &DpfDealerT<EI, EO>::reveal, "out"_a,
             "Receive evaluation results from both Evaluators, XOR-reconstruct into out")
        .def_prop_ro("ell_in", [](const DpfDealerT<EI, EO>&) { return EI; })
        .def_prop_ro("ell_out", [](const DpfDealerT<EI, EO>&) { return EO; });

    dealer_types[EI][EO] = m.attr(name);
}

template <uint64_t ELL_IN, uint64_t ELL_OUT, int PARTY> struct DpfEvaluatorT
{
    static constexpr auto pid = DpfParty<PARTY>::value;
    using EvalType = Bgi16<pid, ELL_IN, ELL_OUT, bind::RVECTOR<ELL_OUT>>;
    using KeyType = typename EvalType::KeyType;

    static constexpr uint64_t ell_in = ELL_IN;
    static constexpr uint64_t ell_out = ELL_OUT;
    static constexpr int party = PARTY;

    DpfEvaluatorT() = default;

    /// Construct with a pre-built IOChannel (non-owning raw pointer).
    DpfEvaluatorT(emp::IOChannel* nio)
        : eval_(std::make_unique<EvalType>(nio))
    {
    }

    ~DpfEvaluatorT() = default;

    std::string recvKey()
    {
        KeyType key = eval_->recvKey();
        return nlohmann::json(key).dump();
    }

    static void eval(const std::string& key_json, nb::object buf_obj, int cores)
    {
        KeyType key = nlohmann::json::parse(key_json).get<KeyType>();
        auto& buf = nb::cast<bind::RVECTOR<ELL_OUT>&>(buf_obj);
        do_eval(key, buf, cores);
    }

    static void eval_range(const std::string& key_json, nb::object buf_obj,
                           uint64_t bg, uint64_t ed, int cores)
    {
        KeyType key = nlohmann::json::parse(key_json).get<KeyType>();
        auto& buf = nb::cast<bind::RVECTOR<ELL_OUT>&>(buf_obj);
        do_range_eval(key, buf, bg, ed, cores);
    }

    void reveal(bind::RVECTOR<ELL_OUT>& buf)
    {
        eval_->reveal(buf);
    }

private:
    static void do_eval(const KeyType& key, bind::RVECTOR<ELL_OUT>& buf, int cores)
    {
        switch (cores)
        {
            case 1:  EvalType::template fullEval<1>(key, buf);  break;
            case 2:  EvalType::template fullEval<2>(key, buf);  break;
            case 4:  EvalType::template fullEval<4>(key, buf);  break;
            case 8:  EvalType::template fullEval<8>(key, buf);  break;
            case 16: EvalType::template fullEval<16>(key, buf); break;
            case 32: EvalType::template fullEval<32>(key, buf); break;
            default: EvalType::template fullEval<1>(key, buf);  break;
        }
    }

    static void do_range_eval(const KeyType& key, bind::RVECTOR<ELL_OUT>& buf,
                              uint64_t bg, uint64_t ed, int cores)
    {
        switch (cores)
        {
            case 1:  EvalType::template rangeEval<1>(key, buf, bg, ed);  break;
            case 2:  EvalType::template rangeEval<2>(key, buf, bg, ed);  break;
            case 4:  EvalType::template rangeEval<4>(key, buf, bg, ed);  break;
            case 8:  EvalType::template rangeEval<8>(key, buf, bg, ed);  break;
            case 16: EvalType::template rangeEval<16>(key, buf, bg, ed); break;
            case 32: EvalType::template rangeEval<32>(key, buf, bg, ed); break;
            default: EvalType::template rangeEval<1>(key, buf, bg, ed);  break;
        }
    }

    std::unique_ptr<EvalType> eval_;
};

template <uint64_t EI, uint64_t EO, int P> static void bind_dpf_evaluator(nb::module_& m)
{
    using namespace nb::literals;
    char name[64];
    std::snprintf(name, sizeof(name), "DpfEvaluator_%lu_%lu_%d", EI, EO, P);

    nb::class_<DpfEvaluatorT<EI, EO, P>>(m, name)
        .def(
            "__init__",
            [](DpfEvaluatorT<EI, EO, P>* self, nb::object dealer_channel) {
                auto* nio = reinterpret_cast<emp::IOChannel*>(
                    nb::cast<uintptr_t>(dealer_channel.attr("acquire")()));
                new (self) DpfEvaluatorT<EI, EO, P>(nio);
            },
            "dealer_channel"_a,
            "Construct a DPF Evaluator from a persistent channel.\n"
            "  dealer_channel  — Channel to the Dealer"
        )
        .def("recv_key", &DpfEvaluatorT<EI, EO, P>::recvKey,
             "Receive DPF key from the Dealer → JSON string")
        .def_static("eval", &DpfEvaluatorT<EI, EO, P>::eval,
             "key_json"_a, "buf"_a.noconvert(), "cores"_a = 1,
             "Evaluate from a JSON key string (no network needed)")
        .def_static("eval_range",
             &DpfEvaluatorT<EI, EO, P>::eval_range,
             "key_json"_a, "buf"_a.noconvert(), "bg"_a, "ed"_a, "cores"_a = 1,
             "Range evaluation: compute only leaves in the specified range. "
             "Supports circular intervals: when ed < bg the range wraps "
             "around as [bg, VEC_LEN) ∪ [0, ed] (output linearised with bg "
             "at offset 0).  When bg ≤ ed the range is [bg, ed]. "
             "bg and ed must be < 2^ELL_IN.  Sub-trees outside the range are pruned.")
        .def("reveal", &DpfEvaluatorT<EI, EO, P>::reveal, "buf"_a,
             "Send evaluation result to the Dealer for reconstruction")
        .def_prop_ro("ell_in", [](const DpfEvaluatorT<EI, EO, P>&) { return EI; })
        .def_prop_ro("ell_out", [](const DpfEvaluatorT<EI, EO, P>&) { return EO; })
        .def_prop_ro("party", [](const DpfEvaluatorT<EI, EO, P>&) { return P; });

    eval_types[EI][EO][P] = m.attr(name);
}

void bind_dpf(nb::module_& m)
{
    using namespace nb::literals;

    bind::for_range<bind::DPF_ELL_IN_MIN, bind::DPF_ELL_IN_MAX>(
        [&]<uint64_t EI>()
        {
            bind::for_range<bind::DPF_ELL_OUT_MIN, bind::DPF_ELL_OUT_MAX>(
                [&]<uint64_t EO>()
                {
                    using Rv = bind::RVECTOR<EO>;
                    static_assert(DpfCnstr<DpfPid::DEALER,      EI, EO, Rv, Bgi16>);
                    static_assert(DpfCnstr<DpfPid::EVALUATOR_0, EI, EO, Rv, Bgi16>);
                    static_assert(DpfCnstr<DpfPid::EVALUATOR_1, EI, EO, Rv, Bgi16>);

                    bind_dpf_dealer<EI, EO>(m);
                    bind_dpf_evaluator<EI, EO, 0>(m);
                    bind_dpf_evaluator<EI, EO, 1>(m);
                }
            );
        }
    );

    m.def(
        "DpfDealer",
        [](uint64_t ei, uint64_t eo) -> nb::object
        {
            if (ei < bind::DPF_ELL_IN_MIN || ei > bind::DPF_ELL_IN_MAX ||
                eo < bind::DPF_ELL_OUT_MIN || eo > bind::DPF_ELL_OUT_MAX)
            {
                throw std::invalid_argument("ell_in / ell_out out of range");
            }
            return dealer_types[ei][eo];
        },
        "ell_in"_a,
        "ell_out"_a,
        "Return the DpfDealer class for the given (ell_in, ell_out)."
    );

    m.def(
        "DpfEvaluator",
        [](uint64_t ei, uint64_t eo, int party) -> nb::object
        {
            if (ei < bind::DPF_ELL_IN_MIN || ei > bind::DPF_ELL_IN_MAX ||
                eo < bind::DPF_ELL_OUT_MIN || eo > bind::DPF_ELL_OUT_MAX)
            {
                throw std::invalid_argument("ell_in / ell_out out of range");
            }
            if (party != 0 && party != 1)
            {
                throw std::invalid_argument("party must be 0 or 1");
            }
            return eval_types[ei][eo][party];
        },
        "ell_in"_a,
        "ell_out"_a,
        "party"_a,
        "Return the DpfEvaluator class for the given (ell_in, ell_out, party)."
    );
}
