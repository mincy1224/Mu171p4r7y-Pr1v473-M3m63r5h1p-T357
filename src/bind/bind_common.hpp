//  Shared bind helpers — ELL ranges, for_range, type aliases.
//
//  
#pragma once

#include <cstdint>
#include <memory>
#include <utility>
#include <Python.h>
#include <nanobind/nanobind.h>

#include <emp-tool/runtime/io/io_channel.h>

#include "ref_impl/dpf/bgi16.hpp"
#include "rvector.hpp"
#include "ref_impl/shr_rep3/aby3.hpp"
#include "ref_impl/shr_add2/emp2.hpp"

namespace nb = nanobind;

namespace scucse::crypto::bind
{

    /// Shared helper: bytes or str → (const uint8_t*, size_t).
    /// @warning The returned pointer is borrowed from the Python object @p h.
    ///          It is only valid while @p h is alive; do not store or defer.
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

    /// Shared helper: extract (const char* data, Py_ssize_t len) from a
    /// Python bytes or bytearray object.  Throws on invalid type.
    /// Consolidates the duplicated PyBytes_Check / PyByteArray_Check pattern.
    inline std::pair<const char*, Py_ssize_t> _getByteBuffer(nb::handle h)
    {
        if (PyBytes_Check(h.ptr()))
            return {PyBytes_AsString(h.ptr()), PyBytes_Size(h.ptr())};
        if (PyByteArray_Check(h.ptr()))
            return {PyByteArray_AsString(h.ptr()), PyByteArray_Size(h.ptr())};
        throw nb::type_error("expected bytes or bytearray");
    }

    /// Mutable version for recv buffers — only accepts bytearray, returns char*.
    inline std::pair<char*, Py_ssize_t> _getWritableByteBuffer(nb::handle h)
    {
        if (!PyByteArray_Check(h.ptr()))
            throw nb::type_error("expected bytearray");
        return {PyByteArray_AsString(h.ptr()), PyByteArray_Size(h.ptr())};
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

    // ———  protocol parameter ranges (single source of truth)  ———
    constexpr uint64_t RVECTOR_ELL_MIN  = 1,                RVECTOR_ELL_MAX = 8;    //  Rvector
    constexpr uint64_t DPF_ELL_IN_MIN   = 13,               DPF_ELL_IN_MAX  = 31;   //  DPF  in
    constexpr uint64_t DPF_ELL_OUT_MIN  = 2,                DPF_ELL_OUT_MAX = 6;    //  DPF  out
    constexpr uint64_t RSS3_ELL_MIN     = 1,                RSS3_ELL_MAX    = 8;    //  ABY3 (3-party RSS)
    constexpr uint64_t ADD2_ELL_MIN     = 2,                ADD2_ELL_MAX    = 31;   //  EMP2 (2-party additive)

    // ———  NetIO shared_ptr registry  ———
    // NetIO instances are managed via std::shared_ptr so that multiple protocol
    // objects can safely share one IOChannel.  Python holds an opaque uintptr_t
    // handle; C++ wrappers call netio_acquire() to obtain a shared_ptr copy.
    std::shared_ptr<emp::IOChannel> netio_acquire(uintptr_t handle);
    void netio_release(uintptr_t handle);
    uintptr_t netio_register(std::shared_ptr<emp::NetIO> sp);

} // namespace scucse::crypto::bind