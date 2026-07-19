//  Shared bind helpers — ELL ranges, for_range, type aliases.
//
//  @author  mincy
#pragma once

#include <cstdint>
#include <utility>
#include <Python.h>
#include <nanobind/nanobind.h>

#include "ref_impl/dpf/bgi16.hpp"
#include "rvector.hpp"
#include "ref_impl/shr_rep3/aby3.hpp"
#include "ref_impl/shr_add2/emp2.hpp"

namespace nb = nanobind;

namespace scucse::crypto::bind
{

    /// Shared helper: bytes or str → (const uint8_t*, size_t)
    inline std::pair<const uint8_t*, size_t> _toBytes(nb::handle h)
    {
        if (PyUnicode_Check(h.ptr())) {
            Py_ssize_t sz;
            const char* s = PyUnicode_AsUTF8AndSize(h.ptr(), &sz);
            return {reinterpret_cast<const uint8_t*>(s), static_cast<size_t>(sz)};
        }
        if (PyBytes_Check(h.ptr())) {
            return {
                reinterpret_cast<const uint8_t*>(PyBytes_AS_STRING(h.ptr())),
                PyBytes_GET_SIZE(h.ptr())
            };
        }
        throw nb::type_error("expected bytes or str");
    }
    
    template <uint64_t ELL> using RVECTOR = math::Rvector<ELL>;

    template <DpfPid PID, uint64_t ELL_IN, uint64_t ELL_OUT>
    using DPF = Bgi16<PID, ELL_IN, ELL_OUT, RVECTOR<ELL_OUT>>;

    template <ShrRep3Pid PID, uint64_t ELL>
    using SHR_RSS3 = Aby3<PID, ELL, RVECTOR>;

    template <ShrAdd2Pid PID, uint64_t ELL>
    using SHR_ADD2 = Emp2<PID, ELL>;

    ///  Iterate fn.operator()<ELL>()  for  ELL ∈ [First, Last].
    template <uint64_t First, uint64_t Last, typename F> constexpr void for_range(F&& fn)
    {
        [&]<uint64_t... Is>(std::index_sequence<Is...>)
        { (fn.template operator()<First + Is>(), ...); }(std::make_index_sequence<Last - First + 1>{});
    }

    // configurable ranges
    // @ Reference table 
    constexpr uint64_t RVECTOR_ELL_MIN  = 1,                RVECTOR_ELL_MAX = 8;                //  Rvector ELL
    constexpr uint64_t DPF_ELL_IN_MIN   = 13,               DPF_ELL_IN_MAX  = 31;               //  DPF
    constexpr uint64_t DPF_ELL_OUT_MIN  = 2,                DPF_ELL_OUT_MAX = 6;
    constexpr uint64_t RSS3_ELL_MIN     = RVECTOR_ELL_MIN,  RSS3_ELL_MAX    = RVECTOR_ELL_MAX;  //  SHR_RSS3  (3-party replicated secret sharing)
    constexpr uint64_t ADD2_ELL_MIN     = 1,                ADD2_ELL_MAX    = 31;               //  SHR_ADD2  (2-party additive secret sharing)

} // namespace scucse::crypto::bind