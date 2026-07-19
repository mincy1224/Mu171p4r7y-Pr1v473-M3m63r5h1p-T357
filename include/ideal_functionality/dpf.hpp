#ifndef DPF_HPP
#define DPF_HPP

#include <cstdint>
#include <memory>
#include <utility>

#include <emp-tool/runtime/io/io_channel.h>
#include "rvector.hpp"

namespace scucse::crypto
{

enum class DpfPid
{
    DEALER,
    EVALUATOR_0,
    EVALUATOR_1,
};

/// @brief Concept for schemes supporting DpfGen + DpfFullEval.
template
    <
        DpfPid PARTY,
        uint64_t ELL_IN,
        uint64_t ELL_OUT,
        typename RVECTOR,
        template <DpfPid, uint64_t, uint64_t, typename> typename SCHEME
    >
    concept DpfCnstr =
        (
            PARTY == DpfPid::DEALER
            && std::constructible_from
            <
                SCHEME<PARTY, ELL_IN, ELL_OUT, RVECTOR>,
                emp::IOChannel*, emp::IOChannel*
            >
            && requires
            (
                SCHEME<PARTY, ELL_IN, ELL_OUT, RVECTOR> scheme,
                const typename SCHEME<PARTY, ELL_IN, ELL_OUT, RVECTOR>::KeyType& key,
                RVECTOR& outRv,
                uint32_t alpha,
                uint8_t  beta
            )
            {
                { scheme.gen(alpha, beta) } -> std::same_as
                        <
                            std::pair
                            <
                                typename SCHEME<PARTY, ELL_IN, ELL_OUT, RVECTOR>::KeyType,
                                typename SCHEME<PARTY, ELL_IN, ELL_OUT, RVECTOR>::KeyType
                            >
                        >;

                { scheme.template sendKey<DpfPid::EVALUATOR_0>(key) } -> std::same_as<void>;
                { scheme.reveal(outRv) } -> std::same_as<void>;
            }
        )
        ||
        (
            PARTY != DpfPid::DEALER
            && std::constructible_from
            <
                SCHEME<PARTY, ELL_IN, ELL_OUT, RVECTOR>,
                emp::IOChannel*
            >
            && requires
            (
                SCHEME<PARTY, ELL_IN, ELL_OUT, RVECTOR> scheme,
                const typename SCHEME<PARTY, ELL_IN, ELL_OUT, RVECTOR>::KeyType& key,
                RVECTOR& bufRv
            )
            {
                { scheme.template fullEval<1>(key, bufRv) } -> std::same_as<void>;
                { scheme.template rangeEval<1>(key, bufRv, 0ULL, 1ULL) } -> std::same_as<void>;
                { scheme.recvKey() } -> std::same_as<typename SCHEME<PARTY, ELL_IN, ELL_OUT, RVECTOR>::KeyType>;
                { scheme.reveal(bufRv) } -> std::same_as<void>;
            }
        );
} // namespace scucse::crypto

#endif // !DPF_HPP
