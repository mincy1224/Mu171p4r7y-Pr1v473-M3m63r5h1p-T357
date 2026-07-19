//  BGI16 — Distributed Point Function.
//
//  @author  mincy
//  @ref     Boyle et al. (https://eprint.iacr.org/2016/622)
#ifndef DPF_BGI16_HPP
#define DPF_BGI16_HPP

#include <bit>
#include <cstdint>
#include <memory>
#include <sodium/core.h>
#include <sodium/randombytes.h>
#include <stdexcept>
#include <thread>
#include <vector>
#include <algorithm>
#include <immintrin.h>
#include <emp-tool/emp-tool.h>
#include <nlohmann/json.hpp>

#include "common.hpp"
#include "ideal_functionality/dpf.hpp"
#include "rvector.hpp"

/// @author artemis
namespace scucse::crypto
{
    template <uint64_t ELL_IN, uint64_t ELL_OUT> class Bgi16Base
    {
        static_assert(ELL_IN <= 32, "ELL_IN must be <= 32: may exceed reasonable memory limits.");
        static_assert(ELL_OUT <= 8, "ELL_OUT must be <= 8 for Rvector output.");

    public:
    protected:
        inline static const uint32_t L = 0;
        inline static const uint32_t R = 1;
        inline static const uint32_t DIR_NUM = 2;

        alignas(16) inline static const __m128i STDMASK_F0 = _mm_setzero_si128();
        alignas(16) inline static const __m128i STDMASK_MSB1 =
            _mm_set_epi64x(0x8000000000000000ULL, 0ULL);
        alignas(16) inline static const __m128i STDMASK_NOT_MSB1 =
            _mm_set_epi64x(0x7FFFFFFFFFFFFFFFLL, 0xFFFFFFFFFFFFFFFFULL);

        struct alignas(16) KeyType
        {
            __m128i stOrigin;
            __m128i cws[ELL_IN][DIR_NUM];
            uint64_t cwLast, forbidDummyPadding;
            uint8_t keyL[16], keyR[16];

            friend void to_json(nlohmann::json& j, const KeyType& k)
            {
                j["stOrigin"] = m128iToHexString(k.stOrigin);

                auto& cws_json = j["cws"];
                cws_json = nlohmann::json::array();
                for (uint64_t i = 0; i < ELL_IN; ++i)
                {
                    auto row = nlohmann::json::array();
                    for (uint32_t d = 0; d < DIR_NUM; ++d)
                    {
                        row.push_back(m128iToHexString(k.cws[i][d]));
                    }
                    cws_json.push_back(std::move(row));
                }

                j["cwLast"] = k.cwLast;
                j["keyL"] = bytesToHexString(k.keyL, 16);
                j["keyR"] = bytesToHexString(k.keyR, 16);
            }

            friend void from_json(const nlohmann::json& j, KeyType& k)
            {
                k.stOrigin = hexStringToM128i(j.at("stOrigin").get<std::string>());

                const auto& cws_json = j.at("cws");
                for (uint64_t i = 0; i < ELL_IN; ++i)
                {
                    const auto& row = cws_json.at(i);
                    for (uint32_t d = 0; d < DIR_NUM; ++d)
                    {
                        k.cws[i][d] = hexStringToM128i(row.at(d).get<std::string>());
                    }
                }

                j.at("cwLast").get_to(k.cwLast);
                Bgi16Base::hexStringToBytes(j.at("keyL").get<std::string>(), k.keyL);
                Bgi16Base::hexStringToBytes(j.at("keyR").get<std::string>(), k.keyR);
            }
        };

        static inline uint32_t getMsb(const __m128i v)
        {
            return static_cast<uint32_t>(_mm_movemask_epi8(v) >> 15);
        }

        static inline uint64_t getTrunc(__m128i v)
        {
            return _mm_cvtsi128_si64(v) & math::RING_MASK8<ELL_OUT>;
        }

        /// Hex string → 16 raw bytes (used by KeyType deserialization).
        static void hexStringToBytes(const std::string& hex, uint8_t* out)
        {
            if (hex.length() < 32)
            {
                throw std::invalid_argument("hex string must be >= 32 chars");
            }
            for (size_t i = 0; i < 16; ++i)
            {
                out[i] = static_cast<uint8_t>(hexCharToNibble(hex[i * 2]) << 4) |
                        hexCharToNibble(hex[i * 2 + 1]);
            }
        }
    };

    template <DpfPid PARTY, uint64_t ELL_IN, uint64_t ELL_OUT, typename RVECTOR>
    class Bgi16 : public Bgi16Base<ELL_IN, ELL_OUT>
    {
    public:
        using KeyType = typename Bgi16Base<ELL_IN, ELL_OUT>::KeyType;
        using Bgi16Base<ELL_IN, ELL_OUT>::L;
        using Bgi16Base<ELL_IN, ELL_OUT>::R;
        using Bgi16Base<ELL_IN, ELL_OUT>::DIR_NUM;
        using Bgi16Base<ELL_IN, ELL_OUT>::getMsb;

        Bgi16(emp::IOChannel* nioToD) : nioToD_(nioToD)
        {
            if (!nioToD_)
            {
                throw std::invalid_argument("Bgi16 Evaluator: IOChannel must not be null");
            }
            if (sodium_init() < 0)
            {
                throw std::runtime_error("lib-sodium init failed.");
            }
        }

        /// Offline full evaluation — no network needed.
        template <uint64_t CORES>
        static void fullEval(const KeyType& key, RVECTOR& fdresBuf)
            requires(PARTY != DpfPid::DEALER)
        {

            emp::CCRH ccrhL(_mm_loadu_si128(reinterpret_cast<const __m128i*>(key.keyL)));
            emp::CCRH ccrhR(_mm_loadu_si128(reinterpret_cast<const __m128i*>(key.keyR)));

            constexpr uint64_t VEC_LEN = 1ULL << ELL_IN;
            if (fdresBuf.size() != VEC_LEN)
            {
                throw std::logic_error("fdresBuf.size() must equal (1 << ELL_IN).");
            }

            constexpr uint64_t LOCAL_BFS_D = (ELL_IN < 8) ? ELL_IN : 8;

            uint64_t splitCoff = std::min<uint64_t>(
                ELL_IN - LOCAL_BFS_D, (std::bit_width(static_cast<uint>(CORES)) - 1) + 3
            );

            uint64_t totalTasks = 1ULL << splitCoff;

            std::vector<__m128i> tasks(totalTasks);
            tasks[0] = key.stOrigin;

            for (uint64_t d = 0; d < splitCoff; ++d)
            {
                uint64_t numNodes = 1ULL << d;
                for (int64_t i = numNodes - 1; i >= 0; --i)
                {
                    __m128i Lch = ccrhL.H(tasks[i]);
                    __m128i Rch = ccrhR.H(tasks[i]);
                    __m128i mulMask = _mm_set1_epi32(-(int32_t) getMsb(tasks[i]));
                    tasks[2 * i] = _mm_xor_si128(Lch, _mm_and_si128(mulMask, key.cws[d][L]));
                    tasks[2 * i + 1] = _mm_xor_si128(Rch, _mm_and_si128(mulMask, key.cws[d][R]));
                }
            }

            auto evalThread = [&tasks, &key, &ccrhL, &ccrhR, &fdresBuf, splitCoff](
                                size_t startTask, size_t endTask)
            {
                privateStkNode stk[(ELL_IN >= LOCAL_BFS_D ? ELL_IN - LOCAL_BFS_D : 1) + 2];
                alignas(16) __m128i bfsBuf[2][1ULL << LOCAL_BFS_D];

                const uint64_t pidMask = (PARTY == DpfPid::EVALUATOR_0) ? 0ULL : ~0ULL;
                const uint64_t cwLast = key.cwLast;

                for (size_t t = startTask; t < endTask; ++t)
                {
                    int stkPtr = 0;
                    stk[stkPtr++] = {tasks[t], t, splitCoff};

                    while (stkPtr > 0)
                    {
                        privateStkNode node = stk[--stkPtr];
                        uint64_t remainingDepth = ELL_IN - node.layer;

                        if (remainingDepth <= LOCAL_BFS_D)
                        {
                            bfsBuf[0][0] = node.st;
                            int cb = 0;

                            for (uint64_t bd = 0; bd < remainingDepth; ++bd)
                            {
                                uint64_t cLayer = node.layer + bd;
                                uint64_t num = 1ULL << bd;
                                int nb = 1 - cb;

                                __m128i cwL = key.cws[cLayer][L];
                                __m128i cwR = key.cws[cLayer][R];

                                uint64_t i = 0;

                                for (; i + 3 < num; i += 4)
                                {
                                    emp::block st_arr[4] = {
                                        bfsBuf[cb][i],
                                        bfsBuf[cb][i + 1],
                                        bfsBuf[cb][i + 2],
                                        bfsBuf[cb][i + 3]
                                    };

                                    emp::block L_arr[4], R_arr[4];
                                    ccrhL.H<4>(L_arr, st_arr);
                                    ccrhR.H<4>(R_arr, st_arr);

                                    __m128i L0 = L_arr[0], L1 = L_arr[1], L2 = L_arr[2], L3 = L_arr[3];
                                    __m128i R0 = R_arr[0], R1 = R_arr[1], R2 = R_arr[2], R3 = R_arr[3];

                                    __m128i m0 = _mm_set1_epi32(-(int32_t) getMsb(bfsBuf[cb][i]));
                                    __m128i m1 =
                                        _mm_set1_epi32(-(int32_t) getMsb(bfsBuf[cb][i + 1]));
                                    __m128i m2 =
                                        _mm_set1_epi32(-(int32_t) getMsb(bfsBuf[cb][i + 2]));
                                    __m128i m3 =
                                        _mm_set1_epi32(-(int32_t) getMsb(bfsBuf[cb][i + 3]));

                                    bfsBuf[nb][2 * i] = _mm_xor_si128(L0, _mm_and_si128(m0, cwL));
                                    bfsBuf[nb][2 * i + 1] = _mm_xor_si128(R0, _mm_and_si128(m0, cwR));
                                    bfsBuf[nb][2 * i + 2] = _mm_xor_si128(L1, _mm_and_si128(m1, cwL));
                                    bfsBuf[nb][2 * i + 3] = _mm_xor_si128(R1, _mm_and_si128(m1, cwR));
                                    bfsBuf[nb][2 * i + 4] = _mm_xor_si128(L2, _mm_and_si128(m2, cwL));
                                    bfsBuf[nb][2 * i + 5] = _mm_xor_si128(R2, _mm_and_si128(m2, cwR));
                                    bfsBuf[nb][2 * i + 6] = _mm_xor_si128(L3, _mm_and_si128(m3, cwL));
                                    bfsBuf[nb][2 * i + 7] = _mm_xor_si128(R3, _mm_and_si128(m3, cwR));
                                }

                                for (; i < num; ++i)
                                {
                                    __m128i Lch = ccrhL.H(bfsBuf[cb][i]);
                                    __m128i Rch = ccrhR.H(bfsBuf[cb][i]);
                                    __m128i m = _mm_set1_epi32(-(int32_t) getMsb(bfsBuf[cb][i]));
                                    bfsBuf[nb][2 * i] = _mm_xor_si128(Lch, _mm_and_si128(m, cwL));
                                    bfsBuf[nb][2 * i + 1] = _mm_xor_si128(Rch, _mm_and_si128(m, cwR));
                                }

                                cb = nb;
                            }

                            uint64_t outStart = node.id << remainingDepth;
                            uint64_t leafCount = 1ULL << remainingDepth;

                            for (uint64_t i = 0; i < leafCount; ++i)
                            {
                                __m128i st = bfsBuf[cb][i];
                                uint64_t msbMask = 0ULL - getMsb(st);
                                uint64_t rawRes = _mm_cvtsi128_si64(st) + (cwLast & msbMask);
                                uint8_t finalRes = static_cast<uint8_t>(
                                    ((rawRes ^ pidMask) - pidMask) & math::RING_MASK8<ELL_OUT>
                                );
                                fdresBuf.set(outStart + i, finalRes);
                            }
                        }
                        else
                        {
                            __m128i Lch = ccrhL.H(node.st);
                            __m128i Rch = ccrhR.H(node.st);
                            __m128i m = _mm_set1_epi32(-(int32_t) getMsb(node.st));

                            stk[stkPtr++] = {
                                _mm_xor_si128(Rch, _mm_and_si128(m, key.cws[node.layer][R])),
                                (node.id << 1) | 1,
                                node.layer + 1
                            };

                            stk[stkPtr++] = {
                                _mm_xor_si128(Lch, _mm_and_si128(m, key.cws[node.layer][L])),
                                (node.id << 1),
                                node.layer + 1
                            };
                        }
                    }
                }
            };

            size_t tasksPerThread = (totalTasks + CORES - 1) / CORES;
            std::vector<std::thread> threads;
            threads.reserve(CORES);

            for (uint32_t i = 0; i < CORES; ++i)
            {
                size_t startTask = i * tasksPerThread;
                size_t endTask = std::min(startTask + tasksPerThread, totalTasks);

                if (startTask >= totalTasks)
                {
                    break;
                }

                threads.emplace_back([&evalThread, startTask, endTask]()
                    {
                        evalThread(startTask, endTask);
                    });
            }

            for (auto& t : threads)
            {
                if (t.joinable())
                {
                    t.join();
                }
            }
        }

        /// Offline range evaluation — computes only leaves in the specified range.
        /// Supports circular intervals: when ed &lt bg the range wraps around:
        ///   [bg, VEC_LEN) ∪ [0, ed]  (output is linearised with bg at offset 0).
        /// When bg ≤ ed the range is the closed interval [bg, ed].
        /// Sub-trees outside the range are pruned.
        template <uint64_t CORES>
        static void rangeEval(const KeyType& key, RVECTOR& fdresBuf,
                              uint64_t bg, uint64_t ed)
            requires(PARTY != DpfPid::DEALER)
        {
            constexpr uint64_t VEC_LEN = 1ULL << ELL_IN;

            // ——— bounds validation ———
            if (bg >= VEC_LEN)
                throw std::invalid_argument("rangeEval: bg out of range");
            if (ed >= VEC_LEN)
                throw std::invalid_argument("rangeEval: ed out of range");

            // circular interval: [bg, VEC_LEN) ∪ [0, ed]  when ed < bg
            // linear  interval: [bg, ed]                    when bg ≤ ed
            const bool isCircular = (ed < bg);
            const uint64_t rangeLen = isCircular
                ? (VEC_LEN - bg) + ed + 1
                : (ed - bg + 1);
            if (fdresBuf.size() != rangeLen)
                throw std::logic_error("rangeEval: fdresBuf.size() must equal range length");

            emp::CCRH ccrhL(_mm_loadu_si128(reinterpret_cast<const __m128i*>(key.keyL)));
            emp::CCRH ccrhR(_mm_loadu_si128(reinterpret_cast<const __m128i*>(key.keyR)));

            constexpr uint64_t LOCAL_BFS_D = (ELL_IN < 8) ? ELL_IN : 8;

            uint64_t splitCoff = std::min<uint64_t>(
                ELL_IN - LOCAL_BFS_D, (std::bit_width(static_cast<uint>(CORES)) - 1) + 3
            );

            uint64_t totalTasks = 1ULL << splitCoff;

            std::vector<__m128i> tasks(totalTasks);
            tasks[0] = key.stOrigin;

            for (uint64_t d = 0; d < splitCoff; ++d)
            {
                uint64_t numNodes = 1ULL << d;
                for (int64_t i = numNodes - 1; i >= 0; --i)
                {
                    __m128i Lch = ccrhL.H(tasks[i]);
                    __m128i Rch = ccrhR.H(tasks[i]);
                    __m128i mulMask = _mm_set1_epi32(-(int32_t) getMsb(tasks[i]));
                    tasks[2 * i] = _mm_xor_si128(Lch, _mm_and_si128(mulMask, key.cws[d][L]));
                    tasks[2 * i + 1] = _mm_xor_si128(Rch, _mm_and_si128(mulMask, key.cws[d][R]));
                }
            }

            auto evalThread = [&tasks, &key, &ccrhL, &ccrhR, &fdresBuf,
                               splitCoff, bg, ed, isCircular]
            (size_t startTask, size_t endTask)
            {
                privateStkNode stk[(ELL_IN >= LOCAL_BFS_D ? ELL_IN - LOCAL_BFS_D : 1) + 2];
                alignas(16) __m128i bfsBuf[2][1ULL << LOCAL_BFS_D];

                const uint64_t pidMask = (PARTY == DpfPid::EVALUATOR_0) ? 0ULL : ~0ULL;
                const uint64_t cwLast = key.cwLast;

                for (size_t t = startTask; t < endTask; ++t)
                {
                    int stkPtr = 0;
                    stk[stkPtr++] = {tasks[t], t, splitCoff};

                    while (stkPtr > 0)
                    {
                        privateStkNode node = stk[--stkPtr];
                        uint64_t remainingDepth = ELL_IN - node.layer;

                        // ——— pruning: skip subtrees outside the range ———
                        uint64_t nodeStart = node.id << remainingDepth;
                        uint64_t nodeEnd   = (node.id + 1) << remainingDepth;
                        // gap for circular is (ed, bg) = [ed+1, bg-1]
                        // gap for linear   is [0, bg) ∪ (ed, VEC_LEN)
                        if (isCircular
                              ? (nodeStart > ed && nodeEnd <= bg)   // fully in (ed, bg)
                              : (nodeEnd <= bg || nodeStart > ed))  // fully outside [bg, ed]
                            continue;

                        if (remainingDepth <= LOCAL_BFS_D)
                        {
                            bfsBuf[0][0] = node.st;
                            int cb = 0;

                            for (uint64_t bd = 0; bd < remainingDepth; ++bd)
                            {
                                uint64_t cLayer = node.layer + bd;
                                uint64_t num = 1ULL << bd;
                                int nb = 1 - cb;

                                __m128i cwL = key.cws[cLayer][L];
                                __m128i cwR = key.cws[cLayer][R];

                                uint64_t i = 0;

                                for (; i + 3 < num; i += 4)
                                {
                                    emp::block st_arr[4] = {
                                        bfsBuf[cb][i],
                                        bfsBuf[cb][i + 1],
                                        bfsBuf[cb][i + 2],
                                        bfsBuf[cb][i + 3]
                                    };

                                    emp::block L_arr[4], R_arr[4];
                                    ccrhL.H<4>(L_arr, st_arr);
                                    ccrhR.H<4>(R_arr, st_arr);

                                    __m128i L0 = L_arr[0], L1 = L_arr[1], L2 = L_arr[2], L3 = L_arr[3];
                                    __m128i R0 = R_arr[0], R1 = R_arr[1], R2 = R_arr[2], R3 = R_arr[3];

                                    __m128i m0 = _mm_set1_epi32(-(int32_t) getMsb(bfsBuf[cb][i]));
                                    __m128i m1 = _mm_set1_epi32(-(int32_t) getMsb(bfsBuf[cb][i + 1]));
                                    __m128i m2 = _mm_set1_epi32(-(int32_t) getMsb(bfsBuf[cb][i + 2]));
                                    __m128i m3 = _mm_set1_epi32(-(int32_t) getMsb(bfsBuf[cb][i + 3]));

                                    bfsBuf[nb][2 * i]     = _mm_xor_si128(L0, _mm_and_si128(m0, cwL));
                                    bfsBuf[nb][2 * i + 1] = _mm_xor_si128(R0, _mm_and_si128(m0, cwR));
                                    bfsBuf[nb][2 * i + 2] = _mm_xor_si128(L1, _mm_and_si128(m1, cwL));
                                    bfsBuf[nb][2 * i + 3] = _mm_xor_si128(R1, _mm_and_si128(m1, cwR));
                                    bfsBuf[nb][2 * i + 4] = _mm_xor_si128(L2, _mm_and_si128(m2, cwL));
                                    bfsBuf[nb][2 * i + 5] = _mm_xor_si128(R2, _mm_and_si128(m2, cwR));
                                    bfsBuf[nb][2 * i + 6] = _mm_xor_si128(L3, _mm_and_si128(m3, cwL));
                                    bfsBuf[nb][2 * i + 7] = _mm_xor_si128(R3, _mm_and_si128(m3, cwR));
                                }

                                for (; i < num; ++i)
                                {
                                    __m128i Lch = ccrhL.H(bfsBuf[cb][i]);
                                    __m128i Rch = ccrhR.H(bfsBuf[cb][i]);
                                    __m128i m = _mm_set1_epi32(-(int32_t) getMsb(bfsBuf[cb][i]));
                                    bfsBuf[nb][2 * i]     = _mm_xor_si128(Lch, _mm_and_si128(m, cwL));
                                    bfsBuf[nb][2 * i + 1] = _mm_xor_si128(Rch, _mm_and_si128(m, cwR));
                                }

                                cb = nb;
                            }

                            uint64_t outStart = nodeStart;  // = node.id << remainingDepth
                            uint64_t leafCount = 1ULL << remainingDepth;

                            for (uint64_t i = 0; i < leafCount; ++i)
                            {
                                uint64_t globalIdx = outStart + i;
                                // leaf-level pruning
                                if (isCircular
                                      ? (globalIdx > ed && globalIdx < bg)  // in gap (ed, bg)
                                      : (globalIdx < bg || globalIdx > ed)) // outside [bg, ed]
                                    continue;

                                __m128i st = bfsBuf[cb][i];
                                uint64_t msbMask = 0ULL - getMsb(st);
                                uint64_t rawRes = _mm_cvtsi128_si64(st) + (cwLast & msbMask);
                                uint8_t finalRes = static_cast<uint8_t>(
                                    ((rawRes ^ pidMask) - pidMask) & math::RING_MASK8<ELL_OUT>
                                );
                                // output offset — circular: [bg..VEC_LEN) then [0..ed]
                                uint64_t offset = isCircular
                                    ? ((globalIdx >= bg)
                                          ? (globalIdx - bg)
                                          : (VEC_LEN - bg) + globalIdx)
                                    : (globalIdx - bg);
                                fdresBuf.set(offset, finalRes);
                            }
                        }
                        else
                        {
                            __m128i Lch = ccrhL.H(node.st);
                            __m128i Rch = ccrhR.H(node.st);
                            __m128i m = _mm_set1_epi32(-(int32_t) getMsb(node.st));

                            uint64_t rChildId = (node.id << 1) | 1;
                            uint64_t rChildLayer = node.layer + 1;
                            uint64_t rRemaining  = ELL_IN - rChildLayer;
                            uint64_t rStart = rChildId << rRemaining;
                            uint64_t rEnd   = (rChildId + 1) << rRemaining;

                            // push right child first (processed second) if it overlaps the range
                            if (isCircular
                                  ? (rEnd > bg || rStart <= ed)     // overlaps [bg, VEC_LEN) or [0, ed]
                                  : (rStart <= ed && rEnd > bg))    // overlaps [bg, ed]
                            {
                                stk[stkPtr++] = {
                                    _mm_xor_si128(Rch, _mm_and_si128(m, key.cws[node.layer][R])),
                                    rChildId,
                                    rChildLayer
                                };
                            }

                            uint64_t lChildId = node.id << 1;
                            uint64_t lChildLayer = node.layer + 1;
                            uint64_t lRemaining  = ELL_IN - lChildLayer;
                            uint64_t lStart = lChildId << lRemaining;
                            uint64_t lEnd   = (lChildId + 1) << lRemaining;

                            // push left child (processed first) if it overlaps the range
                            if (isCircular
                                  ? (lEnd > bg || lStart <= ed)     // overlaps [bg, VEC_LEN) or [0, ed]
                                  : (lStart <= ed && lEnd > bg))    // overlaps [bg, ed]
                            {
                                stk[stkPtr++] = {
                                    _mm_xor_si128(Lch, _mm_and_si128(m, key.cws[node.layer][L])),
                                    lChildId,
                                    lChildLayer
                                };
                            }
                        }
                    }
                }
            };

            size_t tasksPerThread = (totalTasks + CORES - 1) / CORES;
            std::vector<std::thread> threads;
            threads.reserve(CORES);

            for (uint32_t i = 0; i < CORES; ++i)
            {
                size_t startTask = i * tasksPerThread;
                size_t endTask = std::min(startTask + tasksPerThread, totalTasks);

                if (startTask >= totalTasks) break;

                threads.emplace_back([&evalThread, startTask, endTask]()
                    { evalThread(startTask, endTask); });
            }

            for (auto& t : threads)
            {
                if (t.joinable()) t.join();
            }
        }

        KeyType recvKey()
        {
            uint64_t keyStrLen = 0;
            nioToD_->recv_data(&keyStrLen, sizeof(uint64_t));
            std::string keyStr(keyStrLen, '\0');
            nioToD_->recv_data(keyStr.data(), keyStr.size());

            return nlohmann::json::parse(keyStr).get<KeyType>();
        }

        /// Send evaluation result to the Dealer for reconstruction.
        void reveal(const RVECTOR& buf)
        {
            nioToD_->send_data(buf.data(), buf.bytesSize());
            nioToD_->flush();
        }

    private:
        struct alignas(16) privateStkNode
        {
            __m128i st;
            uint64_t id;
            uint64_t layer;
        };

        emp::IOChannel* nioToD_;
    };

    template <uint64_t ELL_IN, uint64_t ELL_OUT, typename RVECTOR>
    class Bgi16<DpfPid::DEALER, ELL_IN, ELL_OUT, RVECTOR> : public Bgi16Base<ELL_IN, ELL_OUT>
    {
    public:
        using KeyType = typename Bgi16Base<ELL_IN, ELL_OUT>::KeyType;
        using Bgi16Base<ELL_IN, ELL_OUT>::L;
        using Bgi16Base<ELL_IN, ELL_OUT>::R;
        using Bgi16Base<ELL_IN, ELL_OUT>::DIR_NUM;
        using Bgi16Base<ELL_IN, ELL_OUT>::STDMASK_F0;
        using Bgi16Base<ELL_IN, ELL_OUT>::STDMASK_MSB1;
        using Bgi16Base<ELL_IN, ELL_OUT>::STDMASK_NOT_MSB1;
        using Bgi16Base<ELL_IN, ELL_OUT>::getMsb;
        using Bgi16Base<ELL_IN, ELL_OUT>::getTrunc;

        Bgi16(emp::IOChannel* nioToE0, emp::IOChannel* nioToE1)
            : nioToE0_(nioToE0), nioToE1_(nioToE1)
        {
            if (!nioToE0_ || !nioToE1_)
            {
                throw std::invalid_argument("Bgi16 Dealer: both IOChannels must not be null");
            }
            if (sodium_init() < 0)
            {
                throw std::runtime_error("lib-sodium init failed.");
            }
        }

        /// Offline key generation — no network needed.
        static std::pair<KeyType, KeyType> gen(uint32_t alpha, uint8_t beta)
        {
            constexpr uint32_t DOMAIN_SIZE = 1ULL << ELL_IN;
            if (alpha >= DOMAIN_SIZE)
                throw std::invalid_argument("gen: alpha out of range");

            constexpr uint32_t PARTY_NUM = 2;
            constexpr uint32_t PID0 = 0;
            constexpr uint32_t PID1 = 1;

            KeyType key[PARTY_NUM];
            alignas(16) __m128i stRoot[PARTY_NUM];

            randombytes_buf(stRoot, sizeof(__m128i) * PARTY_NUM);

            // Generate PRG keys, store in both DPF keys for distribution.
            randombytes_buf(key[PID0].keyL, 16);
            randombytes_buf(key[PID0].keyR, 16);
            std::memcpy(key[PID1].keyL, key[PID0].keyL, 16);
            std::memcpy(key[PID1].keyR, key[PID0].keyR, 16);

            emp::CCRH ccrhL(_mm_loadu_si128(reinterpret_cast<const __m128i*>(key[PID0].keyL)));
            emp::CCRH ccrhR(_mm_loadu_si128(reinterpret_cast<const __m128i*>(key[PID0].keyR)));

    #ifdef __clang__
    #pragma unroll
    #endif
            for (uint32_t pid = 0; pid < PARTY_NUM; ++pid)
            {
                alignas(16) __m128i cbitMask = (pid == PID0) ? STDMASK_F0 : STDMASK_MSB1;
                stRoot[pid] =
                    _mm_xor_si128(_mm_and_si128(STDMASK_NOT_MSB1, stRoot[pid]), cbitMask);
                key[pid].stOrigin = stRoot[pid];
            }

            for (uint64_t i = 0; i < ELL_IN; ++i)
            {
                alignas(16) __m128i stExp[PARTY_NUM][DIR_NUM];

                stExp[PID0][L] = ccrhL.H(stRoot[PID0]);
                stExp[PID0][R] = ccrhR.H(stRoot[PID0]);
                stExp[PID1][L] = ccrhL.H(stRoot[PID1]);
                stExp[PID1][R] = ccrhR.H(stRoot[PID1]);

                uint32_t ai = static_cast<uint32_t>((alpha >> (ELL_IN - 1 - i)) & 1);
                alignas(16) __m128i stCw[DIR_NUM];

                stCw[L] = _mm_and_si128(
                    _mm_xor_si128(stExp[PID0][ai ^ 1], stExp[PID1][ai ^ 1]), STDMASK_NOT_MSB1
                );

                uint32_t cbitR =
                    ai ^ getMsb(_mm_xor_si128(stExp[PID0][R], stExp[PID1][R]));

                stCw[R] =
                    _mm_xor_si128(stCw[L], (cbitR == 0) ? STDMASK_F0 : STDMASK_MSB1);

                uint32_t cbitL =
                    1 ^ ai ^ getMsb(_mm_xor_si128(stExp[PID0][L], stExp[PID1][L]));

                stCw[L] =
                    _mm_xor_si128(stCw[L], (cbitL == 0) ? STDMASK_F0 : STDMASK_MSB1);

    #ifdef __clang__
    #pragma unroll
    #endif
                for (uint32_t pid = 0; pid < PARTY_NUM; ++pid)
                {
                    key[pid].cws[i][L] = stCw[L];
                    key[pid].cws[i][R] = stCw[R];
                    alignas(16) __m128i oblMask = stCw[ai];
                    alignas(16) __m128i mulMask =
                        _mm_set1_epi32(-(int32_t) (getMsb(stRoot[pid])));
                    oblMask = _mm_and_si128(oblMask, mulMask);
                    stRoot[pid] = _mm_xor_si128(oblMask, stExp[pid][ai]);
                }
            }

            uint64_t g0 = getTrunc(stRoot[PID0]);
            uint64_t g1 = getTrunc(stRoot[PID1]);

            key[PID0].cwLast = key[PID1].cwLast = static_cast<uint64_t>(
                ((getMsb(stRoot[PID1]) == 0) ? (beta - g0 + g1) : (g0 - g1 - beta)) &
                math::RING_MASK8<ELL_OUT>
            );

            return {key[PID0], key[PID1]};
        }

        template <DpfPid PARTY>
            requires(PARTY != DpfPid::DEALER)
        void sendKey(const KeyType& key)
        {
            auto& nio = (PARTY == DpfPid::EVALUATOR_0) ? *nioToE0_ : *nioToE1_;
            nlohmann::json keyJson = key;

            const std::string keyStr = keyJson.dump();
            const uint64_t keyStrLen = keyStr.size();
            nio.send_data(&keyStrLen, sizeof(uint64_t));
            nio.flush();
            nio.send_data(keyStr.data(), keyStr.size());
            nio.flush();
        }

        /// Receive evaluation results from both Evaluators and add (mod 2^ELL).
        void reveal(RVECTOR& out)
        {
            RVECTOR e0(out.size()), e1(out.size());
            const size_t sz = out.bytesSize();
            nioToE0_->recv_data(e0.data(), sz);
            nioToE1_->recv_data(e1.data(), sz);
            RVECTOR::add(e0, e1, out);
        }

    private:
        emp::IOChannel* nioToE0_, *nioToE1_;
    };

} // namespace scucse::crypto

#endif // DPF_BGI16_HPP
